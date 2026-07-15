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
import math
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from video_factory import credentials_store

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
    # 瞬时网络故障（下载图片半途断连 IncompleteRead、超时、连接重置）重试一次再放弃。
    # 2026-07-15 真实事故：1.48MB 响应体读到 2/3 断连，IncompleteRead 不在原 except 网里，
    # 裸异常击穿 batch 的"单拍失败跳过"（只接 RuntimeError），整单 385s 白跑。
    # 注意重试=重新生成（断连时服务端可能已计费），一张 ¥0.2 的代价远小于整单报废。
    body = None
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            with urlopen(request, timeout=ARK_TIMEOUT_SECONDS) as response:
                body = json.loads(response.read().decode("utf-8"))
            break
        except HTTPError as exc:  # 明确的 HTTP 错误码：重试大概率无意义，直接报错
            detail = ""
            try:
                detail = (exc.read() or b"").decode("utf-8", errors="replace")[:200]
            except OSError:
                pass
            raise ImageGenError(f"方舟生图 HTTP {exc.code}：{detail}") from exc
        except json.JSONDecodeError as exc:
            raise ImageGenError("方舟生图返回非 JSON 响应。") from exc
        except (URLError, HTTPException, OSError) as exc:
            # 含 IncompleteRead(HTTPException)、超时/连接重置(OSError 族)——瞬时故障，重试。
            last_error = exc
    if body is None:
        raise ImageGenError(f"方舟生图网络失败（已重试 1 次）：{last_error}") from last_error

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


def ingest_generated_image(
    image_bytes: bytes,
    prompt: str,
    category: str,
    tags,
    size: str,
    library_root: Path | str = LIBRARY_ROOT,
) -> Path:
    """拍级路径新生成的图写回图片库并登记 index（与节级共用 _store_image 存储逻辑）。

    2026-07-15 修复：拍级配图上线时只把新图写进任务的 gen_assets/，没有入库——
    库永远养不肥、每单都全额重新生图，违背「生成入库、下次复用省钱」的既定原则。
    单图读写一次 index（26 拍量级开销可忽略），换实现简单与幂等（同 hash 不重复落盘）。
    """
    root = Path(library_root)
    entries = load_index(root)
    item = ImagePlanItem(
        section_index=-1,  # 拍级图不属于特定节；-1 仅占位，index 登记不含此字段
        prompt=str(prompt or "").strip(),
        category=_normalize_category(str(category or "")),
        tags=_normalize_tags(list(tags) if tags else []),
    )
    path = _store_image(image_bytes, item, size, entries, root)
    save_index(entries, root)
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


# --- 配图管家：拍规划 + 拍级匹配（Task B）------------------------------------

# 与 asset_pool.MIN_SECTION_SECONDS 保持一致，确保节时长计算口径统一。
_MIN_SECTION_SECONDS = 2.0


@dataclass(frozen=True)
class Beat:
    """配图拍规划单元：一拍对应一张配图（gen_assets/img_NN）。"""

    section_index: int    # _all_sections_from_rewrite 列表中的节索引（含 hook=0）
    beat_index: int       # 本节内拍序（0-based）
    global_index: int     # 全局拍序（即 img_NN 的 NN）
    narration_slice: str  # 本拍口播文案片段（按字符均分）
    duration: float       # 该拍目标时长（秒）
    section_title: str = ""  # 节标题（用于 LLM 匹配提示）


def _all_sections_from_rewrite(rewrite: dict) -> list[tuple[str, str]]:
    """提取 hook + 正文节，返回 [(title, narration), ...] 含 hook 的全量序列。

    与 assemble._sections_from_rewrite 语义完全一致（空 narration 跳过），
    保证节序与字数分配口径统一，gen_assets 图片数 == --ordered-assets 拍数。
    """
    sections: list[tuple[str, str]] = []
    hook = str(rewrite.get("hook") or "").strip()
    if hook:
        sections.append(("hook", hook))
    for item in rewrite.get("sections") or []:
        if not isinstance(item, dict):
            continue
        narration = str(item.get("narration") or "").strip()
        if not narration:
            continue  # 与 assemble._sections_from_rewrite 对齐
        title = str(item.get("title") or f"第{len(sections) + 1}节").strip()
        sections.append((title, narration))
    return sections


def _beat_char_count(text: str) -> int:
    """去掉空白后的字符数（与 assemble._char_count 同口径）。"""
    return len("".join(str(text or "").split()))


def compute_section_durations(rewrite: dict, target_duration: float) -> list[float]:
    """按各节字数占比把目标时长分配到每节（hook + content）。

    与 asset_pool._split_durations + assemble._char_count 同口径，保证
    batch._run_image_gen 计算的拍数与 assemble --ordered-assets 重新计算的拍数一致。
    """
    sections = _all_sections_from_rewrite(rewrite)
    if not sections:
        return []
    n = len(sections)
    char_counts = [_beat_char_count(narration) for _, narration in sections]
    total_chars = sum(char_counts)
    floor = _MIN_SECTION_SECONDS * n
    flexible = max(0.0, target_duration - floor)
    if total_chars <= 0:
        return [target_duration / n] * n
    durations = [_MIN_SECTION_SECONDS + flexible * c / total_chars for c in char_counts]
    # 浮点残差归到最后一节（与 asset_pool._split_durations 一致）
    durations[-1] += target_duration - sum(durations)
    return durations


def plan_beats(
    rewrite: dict,
    section_durations: list[float],
    beat_seconds: float = 5.0,
) -> list[Beat]:
    """每节按时长计算拍数（ceil(节时长/beat_seconds)），节文案按字符均分到每拍。

    section_durations 与 compute_section_durations() 返回值顺序一致（含 hook）。
    每节至少 1 拍（节时长为 0 时退化为 1 拍 0 秒）。
    """
    sections = _all_sections_from_rewrite(rewrite)
    beats: list[Beat] = []
    global_idx = 0
    for sec_idx, (title, narration) in enumerate(sections):
        if sec_idx >= len(section_durations):
            break
        sec_dur = max(0.0, section_durations[sec_idx])
        n_beats = max(1, math.ceil(sec_dur / beat_seconds) if sec_dur > 0 else 1)
        beat_dur = sec_dur / n_beats
        n_chars = len(narration)
        for b in range(n_beats):
            c_start = round(n_chars * b / n_beats)
            c_end = round(n_chars * (b + 1) / n_beats)
            beats.append(Beat(
                section_index=sec_idx,
                beat_index=b,
                global_index=global_idx,
                narration_slice=narration[c_start:c_end],
                duration=round(beat_dur, 3),
                section_title=title,
            ))
            global_idx += 1
    return beats


def match_beats_to_library(
    beats: list[Beat],
    library_index: list[dict],
    library_root: Path | str = LIBRARY_ROOT,
) -> list[dict] | None:
    """一次 LLM 调用为每拍选库图或给生成提示词；同拍不重复（同 file 第二次改 generate）。

    无 LLM 凭据、回复无法解析、条数不符时返回 None（调用方回落每节1图路径）。
    返回列表每项：{"beat_index": int, "action": "reuse"|"generate",
                   "file": str|None, "prompt": str}
    """
    if not beats:
        return []
    try:
        from video_factory.llm import LLMConfig, chat_completion
        from video_factory.rewrite import resolve_llm_provider
        provider = resolve_llm_provider("auto")
    except RuntimeError:
        return None  # 无 LLM 凭据

    # 给 LLM 看的库图列表（最多 50 条，避免超 token）
    lib_entries = [
        {
            "file": str(e.get("file") or ""),
            "tags": (e.get("tags") or [])[:5],
            "prompt": str(e.get("prompt") or "")[:50],
        }
        for e in (library_index or [])
        if e.get("file")
    ][:50]

    beats_payload = [
        {
            "beat_index": b.global_index,
            "section_title": b.section_title,
            "narration_slice": b.narration_slice,
        }
        for b in beats
    ]

    system_prompt = (
        "你是短视频配图导演，为每一拍分配配图。\n"
        "硬性要求：\n"
        "1. 输出 JSON 数组，与输入拍数等长同序。每个元素：\n"
        '   {"beat_index": 数字, "action": "reuse"|"generate", '
        '"file": "库图文件名（action=reuse 时填库中 file 字段原值；否则填空串）", '
        '"prompt": "中文生图提示词（generate 时 60 字内描述画面主体+构图，禁止画风词；reuse 时填空串）", '
        f'"category": "generate 时从：{"、".join(CATEGORIES)} 中选一（reuse 时填空串）", '
        '"tags": ["generate 时给 3~6 个中文标签（主体/场景/情绪，供图库日后检索复用）；reuse 时空数组"]}\n'
        "2. reuse 要求 file 是图片库里 file 字段的原值；无把握时选 generate。\n"
        "3. 每张库图最多用一次（不同拍要用不同 file）。\n"
        "4. 只输出 JSON 数组，不要其他文字或代码块标记。"
    )
    user_content = json.dumps(
        {"beats": beats_payload, "library": lib_entries},
        ensure_ascii=False,
    )
    try:
        raw = chat_completion(system_prompt, user_content, LLMConfig(provider=provider))
        results = _parse_beat_match_response(raw, beats, library_index)
    except Exception:  # 网络/解析/条数不符 → 全部回落
        return None

    # 代码端去重：同一 file 第二次出现 → 改为 generate
    seen: set[str] = set()
    deduped: list[dict] = []
    for item in results:
        if item["action"] == "reuse":
            f = item.get("file") or ""
            if f and f in seen:
                item = {**item, "action": "generate", "file": None}
            elif f:
                seen.add(f)
        deduped.append(item)
    return deduped


def _parse_beat_match_response(
    raw_text: str,
    beats: list[Beat],
    library_index: list[dict],
) -> list[dict]:
    """解析 LLM 对 match_beats_to_library 的回复；条数或格式不符时抛 ImageGenError。"""
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise ImageGenError(f"LLM 回复中找不到 JSON 数组：{raw_text[:200]}")
    try:
        body = json.loads(text[start: end + 1])
    except json.JSONDecodeError as exc:
        raise ImageGenError(f"LLM 回复 JSON 无法解析：{raw_text[:200]}") from exc
    if not isinstance(body, list) or len(body) != len(beats):
        raise ImageGenError(
            f"LLM 返回条数不匹配：期望 {len(beats)}，"
            f"得到 {len(body) if isinstance(body, list) else '非数组'}。"
        )
    valid_files = {str(e.get("file") or "") for e in library_index if e.get("file")}
    results: list[dict] = []
    for item, beat in zip(body, beats):
        if not isinstance(item, dict):
            raise ImageGenError(f"拍 {beat.global_index} 返回不是对象。")
        action = str(item.get("action") or "generate").strip()
        file_val = str(item.get("file") or "").strip()
        prompt = str(item.get("prompt") or "").strip()
        if action == "reuse" and file_val not in valid_files:
            action = "generate"  # 文件名不在库里 → 改 generate
            file_val = ""
        results.append({
            "beat_index": beat.global_index,
            "action": action,
            "file": file_val if action == "reuse" else None,
            "prompt": prompt,
            # generate 时的入库元数据（缺失/非法宽容归一）：类目回落"场景"、标签可空。
            "category": _normalize_category(str(item.get("category") or "")),
            "tags": list(_normalize_tags(item.get("tags"))),
        })
    return results


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
    # 补齐凭据（ARK_API_KEY 生图 + LLM 归类）：credentials.yaml → 空缺的环境变量。
    credentials_store.ensure_env_loaded()

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
