from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Sequence

from PIL import Image, ImageDraw, ImageFilter

from video_factory.pipeline import _font, _wrap_text_to_pixel_width


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_VIDEO = Path("/Users/king/Downloads/下载 (1).mp4")
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "video_factory/output/cc-switch-deepseek-remix"
WIDTH = 1920
HEIGHT = 1080
FPS = 30


@dataclass(frozen=True)
class CCSwitchDeepSeekScene:
    key: str
    source: str
    start: float
    duration: int
    title: str
    body: str
    bullets: Sequence[str]
    accent: str


def build_cc_switch_deepseek_storyboard() -> List[CCSwitchDeepSeekScene]:
    return [
        CCSwitchDeepSeekScene(
            key="hook",
            source="reference",
            start=0,
            duration=9,
            title="把一件事从头跑通",
            body="用 CC Switch 统一管理 Codex、DeepSeek 和日常 AI 编程工作流。",
            bullets=("CC Switch", "Codex 工作流", "DeepSeek 接入"),
            accent="#31d492",
        ),
        CCSwitchDeepSeekScene(
            key="codex_install",
            source="reference",
            start=64,
            duration=12,
            title="先完成 Codex 环境",
            body="安装完成后，先确认 Codex 能正常打开，基础环境稳定再继续配置。",
            bullets=("安装完成", "环境确认", "减少后续排错"),
            accent="#72e4ff",
        ),
        CCSwitchDeepSeekScene(
            key="cc_switch_intro",
            source="reference",
            start=132,
            duration=12,
            title="CC Switch 是统一入口",
            body="它像一个图形界面的管理台，用来切换模型、供应商和不同 AI 编程工具。",
            bullets=("统一管理", "图形界面", "多工具入口"),
            accent="#f0c95a",
        ),
        CCSwitchDeepSeekScene(
            key="auto_update",
            source="reference",
            start=206,
            duration=10,
            title="工具链要能自动更新",
            body="高质量工作流不是只装一次，而是让版本更新、配置切换都变得可控。",
            bullets=("版本更新", "配置可控", "长期可维护"),
            accent="#a78bfa",
        ),
        CCSwitchDeepSeekScene(
            key="provider_setup",
            source="reference",
            start=275,
            duration=12,
            title="添加 DeepSeek 供应商",
            body="关键是把服务商、接口地址和 API Key 放在同一个配置入口里。",
            bullets=("DeepSeek", "API Key", "接口地址"),
            accent="#31d492",
        ),
        CCSwitchDeepSeekScene(
            key="api_options",
            source="reference",
            start=345,
            duration=14,
            title="每个开关都要有意义",
            body="模型、上下文、权限和开关项，决定 Codex 调用 DeepSeek 时是否稳定。",
            bullets=("模型配置", "上下文设置", "权限控制"),
            accent="#72e4ff",
        ),
        CCSwitchDeepSeekScene(
            key="model_select",
            source="reference",
            start=420,
            duration=12,
            title="选择可用模型并验证",
            body="配置不是填完就结束，必须通过一次真实对话或任务确认模型可用。",
            bullets=("选择模型", "真实验证", "失败及时回滚"),
            accent="#f0c95a",
        ),
        CCSwitchDeepSeekScene(
            key="close",
            source="reference",
            start=490,
            duration=9,
            title="最后得到一套可复用流程",
            body="CC Switch 管入口，Codex 负责任务，DeepSeek 提供模型能力，整套链路才算闭环。",
            bullets=("入口统一", "模型稳定", "流程复用"),
            accent="#31d492",
        ),
    ]


def cc_switch_deepseek_duration(scenes: Sequence[CCSwitchDeepSeekScene]) -> int:
    return sum(scene.duration for scene in scenes)


def build_cc_switch_deepseek_scene_command(
    scene: CCSwitchDeepSeekScene,
    overlay_path: Path | str,
    output_path: Path | str,
) -> List[str]:
    filter_complex = (
        f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS},"
        "eq=contrast=1.06:brightness=0.01:saturation=1.04,"
        "unsharp=5:5:0.45:3:3:0.18[bg];"
        "[1:v]format=rgba[ol];"
        "[bg][ol]overlay=0:0:format=auto,format=yuv420p[v];"
        "[0:a]volume=0.9,aformat=sample_rates=44100:channel_layouts=stereo,"
        "aresample=async=1:first_pts=0[a]"
    )
    return [
        "ffmpeg",
        "-y",
        "-ss",
        _format_time(scene.start),
        "-t",
        str(scene.duration),
        "-i",
        str(SOURCE_VIDEO),
        "-loop",
        "1",
        "-t",
        str(scene.duration),
        "-i",
        str(overlay_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-t",
        str(scene.duration),
        "-r",
        str(FPS),
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        str(output_path),
    ]


def render_cc_switch_deepseek_remix(output_dir: Path = DEFAULT_OUTPUT_DIR) -> Dict[str, Path]:
    if not SOURCE_VIDEO.exists():
        raise FileNotFoundError(f"Source video does not exist: {SOURCE_VIDEO}")

    output_dir = Path(output_dir)
    overlays_dir = output_dir / "overlays"
    segments_dir = output_dir / "segments"
    overlays_dir.mkdir(parents=True, exist_ok=True)
    segments_dir.mkdir(parents=True, exist_ok=True)

    scenes = build_cc_switch_deepseek_storyboard()
    overlays: List[Path] = []
    segments: List[Path] = []
    for index, scene in enumerate(scenes):
        overlay_path = overlays_dir / f"{index:02d}_{scene.key}.png"
        segment_path = segments_dir / f"{index:02d}_{scene.key}.mp4"
        draw_cc_switch_deepseek_overlay(scene, index, len(scenes), overlay_path)
        subprocess.run(build_cc_switch_deepseek_scene_command(scene, overlay_path, segment_path), check=True)
        overlays.append(overlay_path)
        segments.append(segment_path)

    release_path = output_dir / "release.mp4"
    concat_path = output_dir / "segments.txt"
    cover_path = output_dir / "cover.png"
    contact_sheet_path = output_dir / "contact_sheet.jpg"
    script_path = output_dir / "script.md"
    report_path = output_dir / "render_report.json"

    _concat_segments(segments, concat_path, release_path)
    _extract_cover(release_path, cover_path)
    _extract_contact_sheet(release_path, contact_sheet_path)
    _write_script(scenes, script_path)
    _write_report(scenes, overlays, segments, release_path, cover_path, contact_sheet_path, report_path)

    return {
        "video": release_path,
        "cover": cover_path,
        "contact_sheet": contact_sheet_path,
        "script": script_path,
        "report": report_path,
        "concat": concat_path,
    }


def draw_cc_switch_deepseek_overlay(
    scene: CCSwitchDeepSeekScene,
    index: int,
    scene_count: int,
    output_path: Path,
) -> Path:
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    _draw_vignette(image)
    draw = ImageDraw.Draw(image, "RGBA")
    accent = _hex_to_rgb(scene.accent)

    draw.rounded_rectangle((58, 44, 1862, 108), radius=18, fill=(5, 10, 12, 168), outline=(255, 255, 255, 42), width=1)
    draw.text((88, 64), "CC SWITCH WORKFLOW", fill="#f8faf7", font=_font(23, bold=True))
    draw.text((420, 64), "CODEX / DEEPSEEK / API KEY", fill=scene.accent, font=_font(22, bold=True))
    draw.text((1784, 64), f"{index + 1}/{scene_count}", fill="#f8faf7", font=_font(25, bold=True))
    draw.rounded_rectangle((1484, 74, 1728, 88), radius=7, fill=(255, 255, 255, 30))
    draw.rounded_rectangle((1484, 74, 1484 + int(244 * (index + 1) / scene_count), 88), radius=7, fill=(*accent, 238))

    _draw_scene_badge(draw, scene, index)
    _draw_caption_panel(draw, scene)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return output_path


def _draw_scene_badge(draw: ImageDraw.ImageDraw, scene: CCSwitchDeepSeekScene, index: int) -> None:
    accent = _hex_to_rgb(scene.accent)
    box = (68, 136, 626, 330)
    draw.rounded_rectangle(box, radius=24, fill=(3, 8, 10, 154), outline=(*accent, 116), width=1)
    draw.text((98, 162), f"0{index + 1}", fill=scene.accent, font=_font(34, bold=True))
    y = 210
    title_font = _font(34, bold=True)
    for line in _wrap_text_to_pixel_width(scene.title, title_font, 470)[:2]:
        draw.text((98, y), line, fill="#ffffff", font=title_font)
        y += 43


def _draw_caption_panel(draw: ImageDraw.ImageDraw, scene: CCSwitchDeepSeekScene) -> None:
    accent = _hex_to_rgb(scene.accent)
    box = (72, 818, 1848, 1012)
    draw.rounded_rectangle(box, radius=24, fill=(1, 7, 9, 190), outline=(255, 255, 255, 42), width=1)
    draw.rectangle((box[0], box[1], box[0] + 7, box[3]), fill=(*accent, 255))

    body_font = _font(31)
    y = box[1] + 30
    for line in _wrap_text_to_pixel_width(scene.body, body_font, 1140)[:2]:
        draw.text((box[0] + 38, y), line, fill="#f5f8f2", font=body_font)
        y += 43

    bx = 1300
    by = box[1] + 28
    draw.text((bx, by), "KEY STEPS", fill=scene.accent, font=_font(22, bold=True))
    by += 39
    for bullet in scene.bullets[:3]:
        draw.ellipse((bx, by + 4, bx + 18, by + 22), fill=(*accent, 255))
        draw.text((bx + 34, by - 2), bullet, fill="#f5f8f2", font=_font(25, bold=True))
        by += 40


def _draw_vignette(image: Image.Image) -> None:
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    draw.rectangle((0, 0, WIDTH, 128), fill=(0, 0, 0, 86))
    for y in range(770, HEIGHT, 8):
        alpha = min(190, int((y - 770) / (HEIGHT - 770) * 196))
        draw.rectangle((0, y, WIDTH, y + 8), fill=(0, 0, 0, alpha))
    image.alpha_composite(overlay.filter(ImageFilter.GaussianBlur(1)))


def _concat_segments(segment_paths: Sequence[Path], concat_path: Path, output_path: Path) -> None:
    concat_path.write_text(
        "\n".join(f"file '{_escape_concat_path(path.resolve())}'" for path in segment_paths) + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-af",
            "aresample=async=1:first_pts=0",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        check=True,
    )


def _extract_cover(video_path: Path, cover_path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "1", "-i", str(video_path), "-frames:v", "1", "-update", "1", str(cover_path)],
        check=True,
    )


def _extract_contact_sheet(video_path: Path, output_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            "fps=1/12,scale=480:-1,tile=4x2",
            "-frames:v",
            "1",
            "-update",
            "1",
            str(output_path),
        ],
        check=True,
    )


def _write_script(scenes: Sequence[CCSwitchDeepSeekScene], output_path: Path) -> None:
    cursor = 0
    lines = ["# CC Switch + DeepSeek 高质量改编样片脚本", ""]
    for scene in scenes:
        lines.append(f"## {cursor:02d}-{cursor + scene.duration:02d}s  {scene.title}")
        lines.append(f"- Source: {SOURCE_VIDEO}")
        lines.append(f"- Clip: {scene.start}s + {scene.duration}s")
        lines.append(scene.body)
        for bullet in scene.bullets:
            lines.append(f"- {bullet}")
        lines.append("")
        cursor += scene.duration
    output_path.write_text("\n".join(lines), encoding="utf-8")


def _write_report(
    scenes: Sequence[CCSwitchDeepSeekScene],
    overlay_paths: Sequence[Path],
    segment_paths: Sequence[Path],
    release_path: Path,
    cover_path: Path,
    contact_sheet_path: Path,
    report_path: Path,
) -> None:
    payload = {
        "video": {
            "path": str(release_path),
            "width": WIDTH,
            "height": HEIGHT,
            "fps": FPS,
            "duration": cc_switch_deepseek_duration(scenes),
            "rendering_model": "same-topic high-quality remix from real CC Switch tutorial clips",
        },
        "source_video": str(SOURCE_VIDEO),
        "cover": str(cover_path),
        "contact_sheet": str(contact_sheet_path),
        "scenes": [asdict(scene) for scene in scenes],
        "overlays": [str(path) for path in overlay_paths],
        "segments": [str(path) for path in segment_paths],
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _format_time(seconds: float) -> str:
    if float(seconds).is_integer():
        return str(int(seconds))
    return f"{seconds:.3f}"


def _escape_concat_path(path: Path) -> str:
    return str(path).replace("'", r"'\''")


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))
