"""豆包生图图片库（火山方舟 Seedream）：按改写文案自动生图 + 标签化入库复用。

流程定位（可选旁挂，介于 rewrite 与 assemble 之间）：
1. 吃 rewrite.json 的分节（title/narration/visual_hint），一次 LLM 调用为每节产出
   生图提示词 + 视觉类目 + 标签；
2. 每节先按「类目 + 标签重合度 + 尺寸一致」在图片库检索可复用的图，命中不花钱；
3. 未命中才调火山方舟 Seedream 生图（Bearer ARK_API_KEY），产物入库并登记 index.json。
库越大命中率越高，生图成本持续下降——这是图片库存在的意义。

凭据：ARK_API_KEY（火山方舟控制台，**与 TTS 的 VOLC_TTS_APIKEY 不是同一个**）。
缺 Key 时 generate 路径抛 ImageGenError（中文提示），检索复用路径不受影响。
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ARK_API_KEY_ENV = "ARK_API_KEY"
ARK_IMAGE_MODEL_ENV = "ARK_IMAGE_MODEL"
ARK_IMAGE_DEFAULT_MODEL = "doubao-seedream-4-0-250828"
ARK_IMAGES_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
ARK_TIMEOUT_SECONDS = 120

# 图片库根：与视频素材库并列（素材库/图片/<类目>/），index.json 是唯一登记簿。
LIBRARY_ROOT = Path("素材库") / "图片"
INDEX_FILENAME = "index.json"

# 视觉维度类目（用户拍板：按视觉维度分，不沿用内容十类）。文件夹=类目。
CATEGORIES = ("人物", "场景", "物品", "数据图表", "抽象概念", "情绪氛围")
FALLBACK_CATEGORY = "场景"

# 复用判定：类目相同 + 尺寸相同 + 标签重合数 >= 该阈值。
MIN_TAG_OVERLAP = 2

# 竖屏 9:16，2K 级。Seedream 4.0 自定义尺寸要求宽高均 ∈[1280,4096]，
# 1080 宽会低于下限被拒；且按「张」计费与分辨率无关，放大到 2K 不加钱、缩回 1080p
# 视频还留超采样余量，纯提质。
DEFAULT_SIZE = "1440x2560"

# 生图风格提示词：自动拼接到每条生图提示词之后，统一全片画风。
# 优先级：环境变量 IMAGE_STYLE_PROMPT > settings.yaml > 默认值。
# 默认值按用户提供的参考图（抖音 @变强起点 风格）固化：美式漫画/图像小说插画风。
IMAGE_STYLE_PROMPT_ENV = "IMAGE_STYLE_PROMPT"
DEFAULT_STYLE_PROMPT = (
    "美式漫画图像小说插画风格：硬朗清晰的黑色勾线，厚涂与平涂结合的上色，"
    "电影级戏剧光影（强逆光、侧光、窗光），暖橙与冷青撞色氛围，"
    "商务叙事场景感，人物穿深色西装或白衬衫、表情坚毅有张力，"
    "构图突出主体、景深分明，高细节，杂志封面质感"
)


def get_style_prompt() -> str:
    """当前生效的风格提示词：环境变量 > settings.yaml > 内置默认。惰性导入避免环依赖。"""
    env_value = (os.getenv(IMAGE_STYLE_PROMPT_ENV) or "").strip()
    if env_value:
        return env_value
    from video_factory.settings_store import load_settings

    saved = (load_settings().get(IMAGE_STYLE_PROMPT_ENV) or "").strip()
    return saved or DEFAULT_STYLE_PROMPT


class ImageGenError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImagePlanItem:
    """一节的生图需求：提示词 + 类目 + 标签（由 LLM 派生）。"""

    section_index: int
    prompt: str
    category: str
    tags: tuple[str, ...]


# --- LLM 派生每节的生图需求 -----------------------------------------------


def build_image_plan(rewrite: dict) -> list[ImagePlanItem]:
    """一次 LLM 调用：为每个分节产出 {prompt, category, tags}。

    惰性导入 llm/rewrite（与 subtitles 的翻译同款做法），无凭据抛 RewriteError 由调用方转译。
    """
    sections = rewrite.get("sections") or []
    if not sections:
        raise ImageGenError("rewrite.json 里没有分节，无法派生生图需求。")
    from video_factory.llm import LLMConfig, chat_completion
    from video_factory.rewrite import resolve_llm_provider

    provider = resolve_llm_provider("auto")
    categories = "、".join(CATEGORIES)
    system_prompt = (
        "你是短视频配图导演。基于给定的分节口播稿，为每一节设计一张配图。\n"
        "硬性要求：\n"
        "1. 输出 JSON 数组，与输入分节等长同序；每个元素是对象：\n"
        '{"prompt": "中文生图提示词（只描述画面主体+构图+光线+情绪，60字内，禁止出现文字/水印/logo；不要写画风词——画风由系统统一追加）", '
        f'"category": "视觉类目（只能从：{categories} 中选一）", '
        '"tags": ["3到6个中文标签（主体/风格/色调/场景等，用于图库检索复用）"]}\n'
        "2. 提示词要画面化、可直接喂给文生图模型；不要出现具体人名/品牌。\n"
        "3. 只输出 JSON 数组本身，不要输出任何其他文字或代码块标记。"
    )
    payload = [
        {
            "title": str(s.get("title") or ""),
            "narration": str(s.get("narration") or "")[:120],
            "visual_hint": str(s.get("visual_hint") or ""),
        }
        for s in sections
    ]
    raw = chat_completion(system_prompt, json.dumps(payload, ensure_ascii=False), LLMConfig(provider=provider))
    items = _parse_plan_response(raw)
    if len(items) != len(sections):
        raise ImageGenError(f"生图需求条数不匹配：期望 {len(sections)}，LLM 返回 {len(items)}。")
    return [
        ImagePlanItem(
            section_index=i,
            prompt=str(item.get("prompt") or "").strip(),
            category=_normalize_category(str(item.get("category") or "")),
            tags=_normalize_tags(item.get("tags")),
        )
        for i, item in enumerate(items)
    ]


def _parse_plan_response(raw_text: str) -> list[dict]:
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise ImageGenError(f"LLM 回复中找不到 JSON 数组：{raw_text[:200]}")
    try:
        body = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ImageGenError(f"LLM 回复的 JSON 无法解析：{raw_text[:200]}") from exc
    if not isinstance(body, list) or not all(isinstance(x, dict) for x in body):
        raise ImageGenError("LLM 回复不是对象数组。")
    return body


def _normalize_category(category: str) -> str:
    category = category.strip()
    return category if category in CATEGORIES else FALLBACK_CATEGORY


def _normalize_tags(tags) -> tuple[str, ...]:
    if not isinstance(tags, list):
        return ()
    cleaned = []
    for tag in tags:
        text = str(tag or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return tuple(cleaned[:6])


# --- 图片库：index 读写与检索复用 -------------------------------------------


def _index_path(library_root: Path) -> Path:
    return library_root / INDEX_FILENAME


def load_index(library_root: Path | str = LIBRARY_ROOT) -> list[dict]:
    path = _index_path(Path(library_root))
    if not path.exists():
        return []
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []  # 登记簿损坏当作空库（宁可重新生图，不可崩链路）
    entries = body.get("images") if isinstance(body, dict) else None
    return [e for e in (entries or []) if isinstance(e, dict)]


def save_index(entries: list[dict], library_root: Path | str = LIBRARY_ROOT) -> None:
    root = Path(library_root)
    root.mkdir(parents=True, exist_ok=True)
    _index_path(root).write_text(
        json.dumps({"version": "image_index_v1", "images": entries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def find_reusable(
    item: ImagePlanItem,
    size: str,
    entries: list[dict],
    library_root: Path | str = LIBRARY_ROOT,
) -> Path | None:
    """按 类目相同 + 尺寸相同 + 标签重合>=MIN_TAG_OVERLAP 检索可复用图；
    取重合度最高（并列取最新）。文件已被删的条目跳过。"""
    root = Path(library_root)
    best: tuple[int, float, Path] | None = None
    want = set(item.tags)
    for entry in entries:
        if entry.get("category") != item.category or entry.get("size") != size:
            continue
        overlap = len(want & set(entry.get("tags") or []))
        if overlap < MIN_TAG_OVERLAP:
            continue
        path = root / str(entry.get("file") or "")
        if not path.exists():
            continue
        created = float(entry.get("created") or 0.0)
        if best is None or (overlap, created) > (best[0], best[1]):
            best = (overlap, created, path)
    return best[2] if best else None


# --- 火山方舟 Seedream 生图 --------------------------------------------------


def generate_image(prompt: str, size: str = DEFAULT_SIZE) -> bytes:
    """调方舟 images/generations 生一张图，返回图片字节（b64_json 模式免二次下载）。"""
    api_key = (os.getenv(ARK_API_KEY_ENV) or "").strip()
    if not api_key:
        raise ImageGenError(
            f"未配置 {ARK_API_KEY_ENV}（火山方舟控制台的 API Key，与 TTS 的 VOLC_TTS_APIKEY 不是同一个）。"
            "请在工作台「凭据与依赖」页配置后再用生图。"
        )
    model = (os.getenv(ARK_IMAGE_MODEL_ENV) or "").strip() or ARK_IMAGE_DEFAULT_MODEL
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "response_format": "b64_json",
        "watermark": False,
    }
    request = Request(
        ARK_IMAGES_ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Request-Id": str(uuid.uuid4()),
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=ARK_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = ""
        try:
            detail = (exc.read() or b"").decode("utf-8", errors="replace")[:200]
        except OSError:
            pass
        raise ImageGenError(f"方舟生图 HTTP {exc.code}：{detail}") from exc
    except URLError as exc:
        raise ImageGenError(f"方舟生图连接失败：{exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ImageGenError("方舟生图返回非 JSON 响应。") from exc

    data = body.get("data") or []
    if not data or not isinstance(data[0], dict):
        raise ImageGenError(f"方舟生图未返回图片数据：{str(body)[:200]}")
    b64 = data[0].get("b64_json")
    if not b64:
        raise ImageGenError("方舟生图响应缺少 b64_json（请确认模型支持 response_format=b64_json）。")
    try:
        return base64.b64decode(b64)
    except (binascii.Error, ValueError) as exc:
        raise ImageGenError("方舟生图返回的 base64 无法解码。") from exc


def _store_image(
    image_bytes: bytes,
    item: ImagePlanItem,
    size: str,
    entries: list[dict],
    library_root: Path,
) -> Path:
    """图片落库：素材库/图片/<类目>/img_<内容hash>.png + index 登记（同 hash 幂等复用）。"""
    digest = hashlib.sha1(image_bytes).hexdigest()[:16]
    rel = Path(item.category) / f"img_{digest}.png"
    path = library_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(image_bytes)
    entries.append(
        {
            "file": rel.as_posix(),
            "category": item.category,
            "tags": list(item.tags),
            "prompt": item.prompt,
            "size": size,
            "created": round(time.time(), 3),
        }
    )
    return path


# --- 编排：先查库、再生图 ----------------------------------------------------


def ensure_section_images(
    rewrite: dict,
    size: str = DEFAULT_SIZE,
    library_root: Path | str = LIBRARY_ROOT,
) -> dict:
    """为每个分节备一张配图：库命中直接复用，未命中生图入库。

    返回 report dict：{"images": [{"section", "path", "reused", "category", "tags"}...],
    "generated": n, "reused": m}。单节生图失败记 warning 继续（不拖垮整批）。
    """
    root = Path(library_root)
    plan = build_image_plan(rewrite)
    entries = load_index(root)
    style = get_style_prompt()
    results: list[dict] = []
    warnings: list[str] = []
    generated = reused = 0
    for item in plan:
        if not item.prompt:
            warnings.append(f"第 {item.section_index} 节 LLM 未给出生图提示词，跳过。")
            continue
        hit = find_reusable(item, size, entries, root)
        if hit is not None:
            reused += 1
            results.append(_result_row(item, hit, True))
            continue
        try:
            # 主体提示词 + 统一风格提示词：LLM 只管画什么，画风由配置固化（可在
            # 工作台/settings.yaml 改），保证整条片乃至整个账号的画风一致。
            image_bytes = generate_image(f"{item.prompt}。{style}", size)
        except ImageGenError as exc:
            warnings.append(f"第 {item.section_index} 节生图失败：{exc}")
            continue
        path = _store_image(image_bytes, item, size, entries, root)
        generated += 1
        results.append(_result_row(item, path, False))
    save_index(entries, root)
    return {
        "version": "image_gen_report_v1",
        "size": size,
        "images": results,
        "generated": generated,
        "reused": reused,
        "warnings": warnings,
    }


def _result_row(item: ImagePlanItem, path: Path, reused: bool) -> dict:
    return {
        "section": item.section_index,
        "path": str(path),
        "reused": reused,
        "category": item.category,
        "tags": list(item.tags),
        "prompt": item.prompt,
    }


# --- CLI -----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m video_factory.image_gen",
        description="按 rewrite 分节自动配图：图片库命中复用，未命中调豆包（方舟 Seedream）生图入库",
    )
    parser.add_argument("--rewrite", required=True, help="rewrite.json 路径")
    parser.add_argument("--size", default=DEFAULT_SIZE, help=f"图片尺寸（默认 {DEFAULT_SIZE} 竖屏）")
    parser.add_argument("--library", default=str(LIBRARY_ROOT), help="图片库根目录")
    parser.add_argument("--output", default="", help="报告 JSON 落盘路径（可选）")
    args = parser.parse_args(argv)

    try:
        rewrite = json.loads(Path(args.rewrite).read_text(encoding="utf-8"))
        report = ensure_section_images(rewrite, size=args.size, library_root=Path(args.library))
    except (ImageGenError, OSError, json.JSONDecodeError) as exc:
        print(f"配图失败：{exc}")
        return 1
    except RuntimeError as exc:  # 含 rewrite.RewriteError（无 LLM 凭据）
        print(f"配图失败：{exc}")
        return 1

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(f"配图完成：新生成 {report['generated']} 张、库复用 {report['reused']} 张")
    for row in report["images"]:
        mark = "复用" if row["reused"] else "新图"
        print(f"- [{mark}] 第{row['section'] + 1}节 {row['category']}：{row['path']}")
    for warning in report["warnings"]:
        print(f"告警：{warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
