"""发布物料（publish）阶段：封面 + 标题 + 简介 + 标签，一次生成、统一留档。

流程定位：批量链路末位（subtitles 之后），消费 rewrite.json 与成片，产出
publish/ 目录：

- cover_16x9 / cover_9x16 / cover_4x3 / cover_3x4.jpg —— Remotion still 渲染的
  统一模板封面（视频首图做底 + 双线金框 + 大标题；全渠道同模板，主页封面"板正"
  一致）。四画幅各有其位，见 COVER_SPECS 注释——尤其 4x3（横，抖音投稿框免裁）
  与 3x4（竖，抖音主页展示位）方向相反，别搞混；
- publish_kit.json —— 标题候选 / 简介 / 标签 / 封面路径的机器可读留档；
- 发布物料.txt —— 直接复制粘贴用的人类可读版。

降级原则与特效层一致：npx 缺失/封面渲染失败只留痕跳过；LLM 简介失败回落
模板拼接——发布物料永远有产出，绝不因锦上添花的环节阻断批量链路。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
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
# (composition id, 输出文件名, kit 键)；各画幅共用 CoverCard 组件（useVideoConfig 自适应）。
# 抖音横版投稿要两张、方向相反，别搞混：
#   4x3（1440×1080，横）= 投稿时的封面选择框按 4:3 收，传 16:9 会被要求手动裁左右
#                          （2026-07-21 用户实测）；与成片同高、免裁。
#   3x4（1080×1464，竖）= 抖音个人主页作品位是竖向展示，16:9/4:3 都会被中心裁掉左右
#                          （2026-07-16 实测），主页要完整就选这张。
# 16x9 保留：B站/视频号/YouTube 封面仍是 16:9。
COVER_SPECS = (
    ("Cover16x9", "cover_16x9.jpg", "16x9"),
    ("Cover9x16", "cover_9x16.jpg", "9x16"),
    ("Cover4x3", "cover_4x3.jpg", "4x3"),
    ("Cover3x4", "cover_3x4.jpg", "3x4"),
)
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
    """封面底图：优先生图素材首图（构图干净无字），否则从成片 3s 处抽一帧。

    双画幅（2026-07-16）时生图在 9x16/、16x9/ 子目录：依序找第一个非空的
    gen_assets——两张封面共用同一底图，正好保证用户要的"封面一致、首页板正"。
    """
    warnings: list[str] = []
    for gen_dir in (
        job_dir / "gen_assets",
        job_dir / "9x16" / "gen_assets",
        job_dir / "16x9" / "gen_assets",
    ):
        if not gen_dir.is_dir():
            continue
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


def _probe_resolution(path: Path, runner: Runner) -> tuple[int, int]:
    """ffprobe 读 v:0 宽高（判横竖版用）；探测失败返回 (0,0)，调用方留痕跳过。"""
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-print_format", "json", str(path),
    ]
    try:
        completed = runner(command, check=False, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except OSError:
        return (0, 0)
    if getattr(completed, "returncode", 0) != 0:
        return (0, 0)
    try:
        streams = json.loads(getattr(completed, "stdout", "") or "{}").get("streams") or []
        if not streams:
            return (0, 0)
        return (int(streams[0].get("width") or 0), int(streams[0].get("height") or 0))
    except (json.JSONDecodeError, TypeError, ValueError):
        return (0, 0)


# 成片检索优先级（字幕 > 特效 > 原片）与画幅子目录（双画幅链路的落盘位）。
_RELEASE_PRIORITY = ("release_subtitled.mp4", "release_with_effects.mp4", "release.mp4")
_ASPECT_SUBDIRS = ("", "9x16", "16x9")  # "" = 根目录（单画幅链路）


def _archive_videos(
    job_dir: Path, publish_dir: Path, runner: Runner
) -> tuple[dict[str, str], list[str]]:
    """把最终成片归档进 publish/（2026-07-16 用户定案：视频+封面+文案一站式查找）。

    扫描 根目录/9x16/16x9 各自的最优成片，按实际分辨率命名 视频_竖版/横版；
    同盘硬链接零拷贝，跨盘/失败回落复制。任何失败只留痕不阻断。
    """
    archived: dict[str, str] = {}
    warnings: list[str] = []
    for sub in _ASPECT_SUBDIRS:
        base = job_dir / sub if sub else job_dir
        source = next((base / n for n in _RELEASE_PRIORITY if (base / n).exists()), None)
        if source is None:
            continue
        width, height = _probe_resolution(source, runner)
        if width <= 0 or height <= 0:
            warnings.append(f"成片分辨率探测失败，跳过归档：{source}")
            continue
        label = "竖版_9x16" if height > width else "横版_16x9"
        if label in archived:
            continue  # 同向已归档（根目录与子目录并存的遗留场景），先到先得
        dest = publish_dir / f"视频_{label}.mp4"
        try:
            dest.unlink(missing_ok=True)
            try:
                os.link(source, dest)  # 同盘硬链接：零拷贝零额外磁盘
            except OSError:
                shutil.copy2(source, dest)
        except OSError as exc:
            warnings.append(f"成片归档失败（{source} → {dest}）：{exc}")
            continue
        archived[label] = str(dest)
    return archived, warnings


def pick_cover_backgrounds(
    job_dir: Path, video: Path | None, runner: Runner = subprocess.run
) -> tuple[dict[str, Path | None], list[str]]:
    """逐封面按画幅选底图（2026-07-18 用户实锤：16:9 封面配竖图会裁掉人物）。

    候选 = 根/9x16/16x9 三处 gen_assets 的全部图片，按 PIL 真实像素分横竖：
    16x9 封面优先配横图，9x16/3x4 封面优先配竖图；对应向没有图时回落任意首图
    （CoverCard 已改 contain 渲染，人物依然完整，只是留模糊边），再回落成片抽帧。
    """
    warnings: list[str] = []
    landscape: Path | None = None
    portrait: Path | None = None
    first_any: Path | None = None
    for gen_dir in (
        job_dir / "gen_assets",
        job_dir / "9x16" / "gen_assets",
        job_dir / "16x9" / "gen_assets",
    ):
        if not gen_dir.is_dir():
            continue
        for image in sorted(gen_dir.iterdir()):
            if not image.is_file() or image.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                continue
            if first_any is None:
                first_any = image
            if landscape is not None and portrait is not None:
                break
            try:
                from PIL import Image

                with Image.open(image) as img:
                    width, height = img.size
            except Exception:
                continue
            if width > height and landscape is None:
                landscape = image
            elif height >= width and portrait is None:
                portrait = image
    fallback = first_any
    if fallback is None:
        # 全无生图 → 沿用旧逻辑从成片抽帧（三张封面共用）。
        fallback, frame_warnings = pick_cover_background(job_dir, video, runner)
        warnings += frame_warnings
    bgs = {
        "16x9": landscape or fallback,
        "9x16": portrait or fallback,
        "4x3": landscape or fallback,   # 横版封面，配横图
        "3x4": portrait or fallback,
    }
    return bgs, warnings


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
    bgs: dict[str, Path | None] | Path | None,
    output_dir: Path,
    runner: Runner = subprocess.run,
    remotion_dir: Path | str | None = None,
    accent: str = "#e8b84b",
) -> tuple[dict[str, str], list[str]]:
    """Remotion still 渲染三张统一模板封面；npx 缺失/单张失败留痕跳过。

    bgs 支持逐封面底图（{"16x9": 横图, "9x16": 竖图, ...}，2026-07-18 画幅匹配防裁人）；
    传单个 Path/None 时三张共用（向后兼容旧调用）。
    """
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
    if not isinstance(bgs, dict):
        bgs = {key: bgs for _c, _f, key in COVER_SPECS}
    for composition, filename, key in COVER_SPECS:
        bg = bgs.get(key)
        props = {
            "title": title,
            "tag": tag,
            "accent": accent,
            "bg": _data_uri(bg) if bg is not None and bg.exists() else "",
        }
        props_path = output_dir / f"cover.props.{key}.json"
        props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
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
        if "4x3" in covers or "3x4" in covers:
            lines.append("（抖音传横版视频：投稿封面框按 4:3 收，选 4x3 这张免裁；")
            lines.append("  想让主页作品位竖向展示完整，则选 3x4 那张——两张方向相反别搞混）")
    videos = kit.get("videos") or {}
    if videos:
        lines += ["", "【成片】"]
        for key, p in videos.items():
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
    # 逐封面画幅匹配底图（2026-07-18）：16x9 封面配横图、竖封面配竖图，防裁人。
    bgs, bg_warnings = pick_cover_backgrounds(job_dir, video_path, runner)
    warnings += bg_warnings
    covers, cover_warnings = render_covers(
        _cover_title(rewrite), tag, bgs, publish_dir, runner, remotion_dir
    )
    warnings += cover_warnings
    description, tags, desc_warnings = build_description_and_tags(rewrite, provider)
    warnings += desc_warnings
    # 成片归档：横竖版视频硬链接进 publish/，与封面/文案同一文件夹一站式查找。
    archived, archive_warnings = _archive_videos(job_dir, publish_dir, runner)
    warnings += archive_warnings

    kit = {
        "version": "publish_kit_v1",
        "titles": _titles(rewrite),
        "description": description,
        "tags": tags,
        "covers": covers,
        "videos": archived,
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
    print(
        f"发布物料完成：{len(kit['titles'])} 个标题候选，封面 {len(kit['covers'])} 张，"
        f"归档成片 {len(kit.get('videos') or {})} 支"
    )
    print(f"- 物料:     {publish_dir / TXT_FILENAME}")
    print(f"- 留档:     {publish_dir / KIT_FILENAME}")
    for key, path in kit["covers"].items():
        print(f"- 封面{key}: {path}")
    if kit["warnings"]:
        print(f"- 告警 {len(kit['warnings'])} 条（见 kit）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
