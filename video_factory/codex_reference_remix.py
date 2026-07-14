from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Sequence

from PIL import Image, ImageDraw, ImageFilter

from video_factory.pipeline import _font, _wrap_text_to_pixel_width


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_VIDEO = Path("/Users/king/Downloads/下载.mp4")
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "video_factory/output/codex-reference-remix"
WIDTH = 1920
HEIGHT = 1080
FPS = 30


@dataclass(frozen=True)
class CodexReferenceScene:
    key: str
    source: str
    start: float
    duration: int
    title: str
    body: str
    bullets: Sequence[str]
    accent: str


def build_codex_reference_remix_storyboard() -> List[CodexReferenceScene]:
    return [
        CodexReferenceScene(
            key="agent_hook",
            source="reference",
            start=3,
            duration=10,
            title="为什么我要专门做一个 agent",
            body="不是为了炫技，而是把分散的信息、判断标准和操作流程固定下来。",
            bullets=("真实讲解画面", "真实屏幕环境", "主题保持 Codex / AI Skill"),
            accent="#31d492",
        ),
        CodexReferenceScene(
            key="skill_frame",
            source="reference",
            start=52,
            duration=12,
            title="一个 skill，本质是可复用工作流",
            body="它把目标、步骤、素材和输出格式写清楚，让每次执行都能沿着同一套逻辑走。",
            bullets=("明确输入", "固定流程", "降低随机性"),
            accent="#72e4ff",
        ),
        CodexReferenceScene(
            key="knowledge_base",
            source="reference",
            start=102,
            duration=12,
            title="知识库决定回答质量",
            body="AI 不缺生成能力，真正拉开差距的是：它能不能拿到正确上下文。",
            bullets=("资料集中", "证据可追踪", "结果更稳定"),
            accent="#f0c95a",
        ),
        CodexReferenceScene(
            key="framework_answer",
            source="reference",
            start=152,
            duration=12,
            title="把经验变成 framework answer",
            body="好的 agent 不只是回答问题，而是把经验沉淀成可以复用的结构。",
            bullets=("方法可复制", "输出可检查", "团队可复用"),
            accent="#a78bfa",
        ),
        CodexReferenceScene(
            key="open_source_data",
            source="reference",
            start=202,
            duration=14,
            title="从公开资料到结构化推理",
            body="视频里的真实感来自过程：资料、界面、解释和结论都在同一个链路里。",
            bullets=("看得见过程", "画面有信息变化", "字幕解释当前动作"),
            accent="#31d492",
        ),
        CodexReferenceScene(
            key="automation_value",
            source="reference",
            start=252,
            duration=12,
            title="agent 的价值是稳定复用",
            body="一次演示只是样片，把流程固定下来，才可能批量做出同主题视频。",
            bullets=("脚本标准化", "素材标准化", "质检标准化"),
            accent="#72e4ff",
        ),
        CodexReferenceScene(
            key="template_close",
            source="reference",
            start=302,
            duration=8,
            title="把样片变成批量生产模板",
            body="这条视频的主题不变：用 Codex 和 AI Skill，把复杂工作流程变成可重复执行的系统。",
            bullets=("同主题", "真实屏幕", "轻包装"),
            accent="#f0c95a",
        ),
    ]


def codex_reference_remix_duration(scenes: Sequence[CodexReferenceScene]) -> int:
    return sum(scene.duration for scene in scenes)


def build_remix_scene_command(
    scene: CodexReferenceScene,
    overlay_path: Path | str,
    output_path: Path | str,
) -> List[str]:
    filter_complex = (
        f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS},"
        "eq=contrast=1.035:brightness=-0.018:saturation=1.02[bg];"
        "[1:v]format=rgba[ol];"
        "[bg][ol]overlay=0:0:format=auto,format=yuv420p[v];"
        "[0:a]volume=0.82,aformat=sample_rates=44100:channel_layouts=mono[a]"
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
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        str(output_path),
    ]


def render_codex_reference_remix(output_dir: Path = DEFAULT_OUTPUT_DIR) -> Dict[str, Path]:
    if not SOURCE_VIDEO.exists():
        raise FileNotFoundError(f"Source video does not exist: {SOURCE_VIDEO}")

    output_dir = Path(output_dir)
    overlays_dir = output_dir / "overlays"
    segments_dir = output_dir / "segments"
    overlays_dir.mkdir(parents=True, exist_ok=True)
    segments_dir.mkdir(parents=True, exist_ok=True)

    scenes = build_codex_reference_remix_storyboard()
    overlay_paths: List[Path] = []
    segment_paths: List[Path] = []
    for index, scene in enumerate(scenes):
        overlay_path = overlays_dir / f"{index:02d}_{scene.key}.png"
        segment_path = segments_dir / f"{index:02d}_{scene.key}.mp4"
        draw_remix_overlay(scene, index, len(scenes), overlay_path)
        subprocess.run(build_remix_scene_command(scene, overlay_path, segment_path), check=True)
        overlay_paths.append(overlay_path)
        segment_paths.append(segment_path)

    release_path = output_dir / "release.mp4"
    concat_path = output_dir / "segments.txt"
    cover_path = output_dir / "cover.png"
    contact_sheet_path = output_dir / "contact_sheet.jpg"
    script_path = output_dir / "script.md"
    report_path = output_dir / "render_report.json"
    _concat_segments(segment_paths, concat_path, release_path)
    _extract_cover(release_path, cover_path)
    _extract_contact_sheet(release_path, contact_sheet_path)
    _write_script(scenes, script_path)
    _write_report(scenes, overlay_paths, segment_paths, release_path, cover_path, contact_sheet_path, report_path)
    return {
        "video": release_path,
        "cover": cover_path,
        "contact_sheet": contact_sheet_path,
        "script": script_path,
        "report": report_path,
        "concat": concat_path,
    }


def draw_remix_overlay(
    scene: CodexReferenceScene,
    index: int,
    scene_count: int,
    output_path: Path,
) -> Path:
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    _draw_vignette(image)
    draw = ImageDraw.Draw(image, "RGBA")
    accent = _hex_to_rgb(scene.accent)

    draw.rounded_rectangle((56, 46, 1864, 108), radius=18, fill=(4, 12, 10, 164), outline=(255, 255, 255, 40), width=1)
    draw.text((84, 65), "AI SKILL WORKFLOW", fill="#f7faf6", font=_font(23, bold=True))
    draw.text((392, 65), "CODEX / AGENT / KNOWLEDGE BASE", fill=scene.accent, font=_font(22, bold=True))
    draw.text((1786, 65), f"{index + 1}/{scene_count}", fill="#f7faf6", font=_font(25, bold=True))
    draw.rounded_rectangle((1500, 75, 1732, 88), radius=7, fill=(255, 255, 255, 30))
    draw.rounded_rectangle((1500, 75, 1500 + int(232 * (index + 1) / scene_count), 88), radius=7, fill=(*accent, 238))

    _draw_chapter_card(draw, scene, index)
    _draw_lower_caption(draw, scene)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return output_path


def _draw_chapter_card(draw: ImageDraw.ImageDraw, scene: CodexReferenceScene, index: int) -> None:
    accent = _hex_to_rgb(scene.accent)
    box = (68, 136, 626, 332)
    draw.rounded_rectangle(box, radius=24, fill=(2, 9, 8, 156), outline=(*accent, 112), width=1)
    draw.text((98, 164), f"0{index + 1}", fill=scene.accent, font=_font(34, bold=True))
    y = 210
    title_font = _font(34, bold=True)
    for line in _wrap_text_to_pixel_width(scene.title, title_font, 470)[:2]:
        draw.text((98, y), line, fill="#ffffff", font=title_font)
        y += 43


def _draw_lower_caption(draw: ImageDraw.ImageDraw, scene: CodexReferenceScene) -> None:
    accent = _hex_to_rgb(scene.accent)
    box = (72, 822, 1848, 1010)
    draw.rounded_rectangle(box, radius=24, fill=(1, 7, 7, 188), outline=(255, 255, 255, 42), width=1)
    draw.rectangle((box[0], box[1], box[0] + 7, box[3]), fill=(*accent, 255))

    body_font = _font(31)
    y = box[1] + 30
    for line in _wrap_text_to_pixel_width(scene.body, body_font, 1130)[:2]:
        draw.text((box[0] + 38, y), line, fill="#f4f8f2", font=body_font)
        y += 43

    bx = 1290
    by = box[1] + 28
    draw.text((bx, by), "KEY POINTS", fill=scene.accent, font=_font(22, bold=True))
    by += 38
    for bullet in scene.bullets[:3]:
        draw.ellipse((bx, by + 4, bx + 18, by + 22), fill=(*accent, 255))
        draw.text((bx + 34, by - 2), bullet, fill="#f4f8f2", font=_font(25, bold=True))
        by += 40


def _draw_vignette(image: Image.Image) -> None:
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    draw.rectangle((0, 0, WIDTH, 128), fill=(0, 0, 0, 88))
    for y in range(770, HEIGHT, 8):
        alpha = min(190, int((y - 770) / (HEIGHT - 770) * 195))
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
            "fps=1/10,scale=480:-1,tile=4x2",
            "-frames:v",
            "1",
            "-update",
            "1",
            str(output_path),
        ],
        check=True,
    )


def _write_script(scenes: Sequence[CodexReferenceScene], output_path: Path) -> None:
    cursor = 0
    lines = ["# Codex 同主题改编样片脚本", ""]
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
    scenes: Sequence[CodexReferenceScene],
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
            "duration": codex_reference_remix_duration(scenes),
            "rendering_model": "same-topic remix from real source video clips plus transparent overlays",
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
