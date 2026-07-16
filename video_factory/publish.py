"""发布物料（publish）阶段：封面 + 标题 + 简介 + 标签，一次生成、统一留档。

流程定位：批量链路末位（subtitles 之后），消费 rewrite.json 与成片，产出
publish/ 目录：

- cover_16x9.jpg / cover_9x16.jpg —— Remotion still 渲染的统一模板封面
  （视频首图做底 + 双线金框 + 大标题；全渠道同模板，主页封面"板正"一致）；
- publish_kit.json —— 标题候选 / 简介 / 标签 / 封面路径的机器可读留档；
- 发布物料.txt —— 直接复制粘贴用的人类可读版。

降级原则与特效层一致：npx 缺失/封面渲染失败只留痕跳过；LLM 简介失败回落
模板拼接——发布物料永远有产出，绝不因锦上添花的环节阻断批量链路。
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from video_factory import credentials_store, stage_report
from video_factory.llm import LLMConfig, LLMProviderError, chat_completion
from video_factory.rewrite import RewriteError, resolve_llm_provider

Runner = Callable[..., subprocess.CompletedProcess]

PUBLISH_DIRNAME = "publish"
KIT_FILENAME = "publish_kit.json"
TXT_FILENAME = "发布物料.txt"
# (composition id, 输出文件名)；两画幅共用 CoverCard 组件。
COVER_SPECS = (("Cover16x9", "cover_16x9.jpg"), ("Cover9x16", "cover_9x16.jpg"))
COVER_TITLE_MAX_CHARS = 20   # 封面标题超长截断（组件内还会拆 ≤2 行）
DESCRIPTION_MAX_CHARS = 160  # 平台简介长度上限（大多数平台 200 字内，留余量）
TAGS_MAX = 6

_FF_QUIET = ("-hide_banner", "-loglevel", "error")


class PublishError(RuntimeError):
    pass


def _load_json(path: Path, label: str) -> dict:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PublishError(f"{label}不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise PublishError(f"{label}无法解析：{path}") from exc
    if not isinstance(body, dict):
        raise PublishError(f"{label}内容不是 JSON 对象：{path}")
    return body


def pick_cover_background(
    job_dir: Path, video: Path | None, runner: Runner = subprocess.run
) -> tuple[Path | None, list[str]]:
    """封面底图：优先生图素材首图（构图干净无字），否则从成片 3s 处抽一帧。"""
    warnings: list[str] = []
    gen_dir = job_dir / "gen_assets"
    if gen_dir.is_dir():
        images = sorted(
            p for p in gen_dir.iterdir()
            if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
        )
        if images:
            return images[0], warnings
    if video is not None and video.exists():
        out = job_dir / PUBLISH_DIRNAME / "cover_bg.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "ffmpeg", "-y", *_FF_QUIET,
            "-ss", "3", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out),
        ]
        try:
            completed = runner(command, check=False, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
        except OSError as exc:
            warnings.append(f"封面底图抽帧无法启动 ffmpeg：{exc}")
            return None, warnings
        if getattr(completed, "returncode", 0) == 0 and out.exists():
            return out, warnings
        warnings.append("封面底图抽帧失败，封面改用纯深色底。")
    return None, warnings


def _data_uri(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _cover_title(rewrite: dict) -> str:
    titles = rewrite.get("publish_titles")
    if isinstance(titles, list):
        for title in titles:
            text = str(title or "").strip()
            if text:
                return text[:COVER_TITLE_MAX_CHARS]
    return str(rewrite.get("hook") or "").strip()[:COVER_TITLE_MAX_CHARS]


def render_covers(
    title: str,
    tag: str,
    bg: Path | None,
    output_dir: Path,
    runner: Runner = subprocess.run,
    remotion_dir: Path | str | None = None,
    accent: str = "#e8b84b",
) -> tuple[dict[str, str], list[str]]:
    """Remotion still 渲染两张统一模板封面；npx 缺失/单张失败留痕跳过。"""
    warnings: list[str] = []
    covers: dict[str, str] = {}
    npx = shutil.which("npx")
    if not npx:
        warnings.append("未找到 npx，跳过封面渲染（发布物料其余部分照常产出）。")
        return covers, warnings
    project = Path(remotion_dir) if remotion_dir else Path(__file__).resolve().parent.parent / "remotion"
    entry = project / "src" / "index.ts"
    # 必须绝对路径：npx remotion still 以 remotion/ 为 cwd 运行，相对输出路径会把
    # jpg 写到 remotion/ 下的错误位置（render_effects 同款坑，2026-07-16 实测复现）。
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    props = {
        "title": title,
        "tag": tag,
        "accent": accent,
        "bg": _data_uri(bg) if bg is not None and bg.exists() else "",
    }
    props_path = output_dir / "cover.props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    for composition, filename in COVER_SPECS:
        out_path = output_dir / filename
        command = [
            npx, "remotion", "still", str(entry), composition, str(out_path),
            f"--props={props_path.resolve().as_posix()}",
        ]
        try:
            completed = runner(command, check=False, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", cwd=str(project))
        except OSError as exc:
            warnings.append(f"封面 {composition} 渲染无法启动 npx：{exc}")
            continue
        if getattr(completed, "returncode", 0) != 0:
            stderr = (getattr(completed, "stderr", "") or "")[-300:]
            warnings.append(f"封面 {composition} 渲染失败（退出码 {completed.returncode}）：{stderr}")
            continue
        if not out_path.exists():
            warnings.append(f"封面 {composition} 渲染声称成功但文件未落盘：{out_path}")
            continue
        key = "16x9" if composition == "Cover16x9" else "9x16"
        covers[key] = str(out_path)
    return covers, warnings


def _fallback_description(rewrite: dict) -> str:
    """LLM 不可用时的模板简介：hook + 前几节小标题串联。"""
    hook = str(rewrite.get("hook") or "").strip()
    section_titles = [
        str(s.get("title") or "").strip()
        for s in (rewrite.get("sections") or [])
        if isinstance(s, dict) and str(s.get("title") or "").strip()
    ]
    parts = [hook] if hook else []
    if section_titles:
        parts.append("本期讲透：" + "、".join(section_titles[:4]) + "。")
    return "".join(parts)[:DESCRIPTION_MAX_CHARS]


def _fallback_tags(rewrite: dict) -> list[str]:
    """标签兜底：emphasis 文本里 ≤8 字的短语去重取前几个。"""
    tags: list[str] = []
    for section in rewrite.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for em in section.get("emphasis") or []:
            if isinstance(em, dict):
                text = str(em.get("text") or "").strip()
                if text and len(text) <= 8 and text not in tags:
                    tags.append(text)
    return tags[:TAGS_MAX]


def build_description_and_tags(
    rewrite: dict, provider: str = ""
) -> tuple[str, list[str], list[str]]:
    """LLM 生成平台简介与话题标签；任何失败降级模板拼接（留痕）。"""
    warnings: list[str] = []
    voiceover = "\n".join(
        str(s.get("narration") or "")
        for s in (rewrite.get("sections") or [])
        if isinstance(s, dict)
    )
    system_prompt = (
        "你是短视频发布运营。根据口播文案写发布物料，只输出 JSON："
        '{"description": "80~140字的平台简介，口语化、带悬念、结尾引导完整观看，不要话题标签", '
        '"tags": ["3~6个话题标签，每个2~8字，不带#号"]}'
    )
    user_prompt = f"标题：{_cover_title(rewrite)}\n口播文案：\n{voiceover[:2000]}"
    try:
        config = LLMConfig(provider=resolve_llm_provider(provider or "auto"))
        reply = chat_completion(system_prompt, user_prompt, config)
        start, end = reply.find("{"), reply.rfind("}")
        body = json.loads(reply[start:end + 1])
        description = str(body.get("description") or "").strip()[:DESCRIPTION_MAX_CHARS]
        tags = [
            str(t).strip().lstrip("#") for t in (body.get("tags") or [])
            if str(t).strip()
        ][:TAGS_MAX]
        if description:
            return description, tags or _fallback_tags(rewrite), warnings
        warnings.append("LLM 简介为空，已降级模板拼接。")
    except (LLMProviderError, RewriteError, json.JSONDecodeError, ValueError, KeyError) as exc:
        warnings.append(f"LLM 简介生成失败，已降级模板拼接：{exc}")
    return _fallback_description(rewrite), _fallback_tags(rewrite), warnings


def _titles(rewrite: dict) -> list[str]:
    titles = [
        str(t or "").strip()
        for t in (rewrite.get("publish_titles") or [])
        if str(t or "").strip()
    ]
    if not titles:
        hook = str(rewrite.get("hook") or "").strip()
        if hook:
            titles = [hook]
    return titles


def _write_txt(path: Path, kit: dict) -> None:
    lines: list[str] = ["【标题候选】"]
    for i, title in enumerate(kit["titles"], 1):
        lines.append(f"{i}. {title}")
    lines += ["", "【简介】", kit["description"], "", "【标签】",
              " ".join(f"#{t}" for t in kit["tags"])]
    covers = kit.get("covers") or {}
    if covers:
        lines += ["", "【封面】"]
        for key, p in covers.items():
            lines.append(f"{key}: {p}")
    if kit.get("warnings"):
        lines += ["", "【告警】"] + [f"- {w}" for w in kit["warnings"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_publish_kit(
    rewrite_path: Path | str,
    video: Path | str | None,
    output_dir: Path | str,
    tag: str = "",
    provider: str = "",
    runner: Runner = subprocess.run,
    remotion_dir: Path | str | None = None,
) -> dict:
    """编排：底图 → 双封面 → 标题/简介/标签 → 落 publish/ 留档。返回 kit dict。"""
    rewrite = _load_json(Path(rewrite_path), "rewrite.json")
    job_dir = Path(output_dir)
    publish_dir = job_dir / PUBLISH_DIRNAME
    publish_dir.mkdir(parents=True, exist_ok=True)
    video_path = Path(video) if video else None

    warnings: list[str] = []
    bg, bg_warnings = pick_cover_background(job_dir, video_path, runner)
    warnings += bg_warnings
    covers, cover_warnings = render_covers(
        _cover_title(rewrite), tag, bg, publish_dir, runner, remotion_dir
    )
    warnings += cover_warnings
    description, tags, desc_warnings = build_description_and_tags(rewrite, provider)
    warnings += desc_warnings

    kit = {
        "version": "publish_kit_v1",
        "titles": _titles(rewrite),
        "description": description,
        "tags": tags,
        "covers": covers,
        "warnings": warnings,
    }
    (publish_dir / KIT_FILENAME).write_text(
        json.dumps(kit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_txt(publish_dir / TXT_FILENAME, kit)
    return kit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m video_factory.publish",
        description="发布物料：统一模板双封面 + 标题/简介/标签，留档 publish/",
    )
    parser.add_argument("--rewrite", required=True, help="rewrite.json 路径")
    parser.add_argument("--video", default="", help="最终成片路径（封面底图抽帧兜底用）")
    parser.add_argument("--output", required=True, help="job 目录（publish/ 落在其下）")
    parser.add_argument("--tag", default="", help="封面右上品牌角标文字（空=隐藏）")
    parser.add_argument("--llm", default="", help="简介生成的 LLM provider（空=auto）")
    args = parser.parse_args(argv)
    credentials_store.ensure_env_loaded()

    try:
        kit = generate_publish_kit(
            Path(args.rewrite),
            Path(args.video) if args.video else None,
            Path(args.output),
            tag=args.tag,
            provider=args.llm,
        )
    except (PublishError, OSError) as exc:
        print(f"发布物料生成失败：{exc}")
        stage_report.write_stage_error(args.output, "publish", f"发布物料生成失败：{exc}")
        return 1

    publish_dir = Path(args.output) / PUBLISH_DIRNAME
    print(f"发布物料完成：{len(kit['titles'])} 个标题候选，封面 {len(kit['covers'])} 张")
    print(f"- 物料:     {publish_dir / TXT_FILENAME}")
    print(f"- 留档:     {publish_dir / KIT_FILENAME}")
    for key, path in kit["covers"].items():
        print(f"- 封面{key}: {path}")
    if kit["warnings"]:
        print(f"- 告警 {len(kit['warnings'])} 条（见 kit）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
