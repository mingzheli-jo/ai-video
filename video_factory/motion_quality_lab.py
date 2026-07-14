from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Sequence

from PIL import Image, ImageDraw, ImageFilter

from video_factory.pipeline import _font, _wrap_text_to_pixel_width


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_VIDEO = Path("/Users/king/Downloads/下载.mp4")
FAILED_SAMPLE_VIDEO = PROJECT_ROOT / "video_factory/output/portugal-dr-congo-reference-clean/release.mp4"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "video_factory/output/quality-lab-motion-sample"
WIDTH = 1920
HEIGHT = 1080
FPS = 30


@dataclass(frozen=True)
class MotionQualityLabScene:
    key: str
    source: str
    start: float
    duration: int
    title: str
    body: str
    bullets: Sequence[str]
    accent: str


def build_motion_quality_lab_storyboard() -> List[MotionQualityLabScene]:
    return [
        MotionQualityLabScene(
            key="hook_split",
            source="split",
            start=3,
            duration=9,
            title="这次不用图片轮播，直接看运动画面",
            body="左边是你给的参考视频，右边是上一版失败样片。问题不是码率，而是内容语义没有统一。",
            bullets=("真实视频底片", "同屏对照", "只用字幕做诊断覆盖"),
            accent="#31d492",
        ),
        MotionQualityLabScene(
            key="reference_anchor",
            source="reference",
            start=42,
            duration=12,
            title="参考片的真实感来自连续动作",
            body="人、屏幕、字幕、语音在同一个空间里连续发生，观众会默认它可信。",
            bullets=("人物动作连续", "屏幕内容能被看见", "字幕解释当前画面"),
            accent="#72e4ff",
        ),
        MotionQualityLabScene(
            key="reference_process",
            source="reference",
            start=96,
            duration=12,
            title="它不是素材堆叠，而是过程展示",
            body="参考片一直在给观众看操作、界面和结果，所以包装可以很少，可信度反而更强。",
            bullets=("过程可见", "信息块有因果", "镜头服务讲解"),
            accent="#f0c95a",
        ),
        MotionQualityLabScene(
            key="failed_motion",
            source="failed",
            start=0,
            duration=14,
            title="上一版为什么像 AI：底片和主题打架",
            body="底片在讲 Codex，覆盖层却在讲葡萄牙比分。画面在动，但证据链断了。",
            bullets=("运动不等于真实", "主题不同源", "贴片越多越假"),
            accent="#ff6b6b",
        ),
        MotionQualityLabScene(
            key="reference_density",
            source="reference",
            start=150,
            duration=12,
            title="真正要复制的是这种素材密度",
            body="每几秒都有可观察的信息变化：人说话、屏幕变、字幕解释、画面回应。",
            bullets=("镜头有信息增量", "不是静态展示", "不是用大字遮丑"),
            accent="#a78bfa",
        ),
        MotionQualityLabScene(
            key="workflow",
            source="reference",
            start=248,
            duration=12,
            title="批量生产必须先解决素材同源",
            body="Codex 可以批量剪辑、叠字幕、做质检，但它不能把无关素材变成真实证据。",
            bullets=("先建素材包", "再写镜头脚本", "最后自动合成和质检"),
            accent="#31d492",
        ),
        MotionQualityLabScene(
            key="close",
            source="reference",
            start=300,
            duration=9,
            title="下一版样片必须是同主题真实视频",
            body="如果做比赛，就用合规足球素材；如果做 Codex，就继续用真实产品演示。不能再用静态图假装视频。",
            bullets=("底片要会动", "主题要同源", "字幕只做增强"),
            accent="#f0c95a",
        ),
    ]


def motion_quality_lab_duration(scenes: Sequence[MotionQualityLabScene]) -> int:
    return sum(scene.duration for scene in scenes)


def build_motion_scene_command(
    scene: MotionQualityLabScene,
    overlay_path: Path | str,
    output_path: Path | str,
) -> List[str]:
    if scene.source == "split":
        return _build_split_scene_command(scene, overlay_path, output_path)
    return _build_single_source_scene_command(scene, overlay_path, output_path)


def render_motion_quality_lab_sample(output_dir: Path = DEFAULT_OUTPUT_DIR) -> Dict[str, Path]:
    _assert_source_videos()
    output_dir = Path(output_dir)
    overlays_dir = output_dir / "overlays"
    segments_dir = output_dir / "segments"
    overlays_dir.mkdir(parents=True, exist_ok=True)
    segments_dir.mkdir(parents=True, exist_ok=True)

    scenes = build_motion_quality_lab_storyboard()
    segment_paths: List[Path] = []
    overlay_paths: List[Path] = []
    for index, scene in enumerate(scenes):
        overlay_path = overlays_dir / f"{index:02d}_{scene.key}.png"
        segment_path = segments_dir / f"{index:02d}_{scene.key}.mp4"
        draw_motion_overlay(scene, index, len(scenes), overlay_path)
        subprocess.run(build_motion_scene_command(scene, overlay_path, segment_path), check=True)
        overlay_paths.append(overlay_path)
        segment_paths.append(segment_path)

    release_path = output_dir / "release.mp4"
    concat_path = output_dir / "segments.txt"
    cover_path = output_dir / "cover.png"
    report_path = output_dir / "render_report.json"
    script_path = output_dir / "script.md"
    _concat_segments(segment_paths, concat_path, release_path)
    _extract_cover(release_path, cover_path)
    _write_motion_script(scenes, script_path)
    _write_motion_report(scenes, overlay_paths, segment_paths, release_path, cover_path, report_path)
    return {
        "video": release_path,
        "cover": cover_path,
        "script": script_path,
        "report": report_path,
        "concat": concat_path,
    }


def draw_motion_overlay(
    scene: MotionQualityLabScene,
    index: int,
    scene_count: int,
    output_path: Path,
) -> Path:
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image, "RGBA")
    accent_rgb = _hex_to_rgb(scene.accent)

    _draw_vignette(image)
    draw.rounded_rectangle((58, 46, 1862, 108), radius=18, fill=(4, 11, 10, 176), outline=(255, 255, 255, 42), width=1)
    draw.text((86, 65), "CODEX VIDEO QUALITY LAB", fill="#f8faf7", font=_font(23, bold=True))
    draw.text((1518, 65), f"{index + 1}/{scene_count}", fill="#f8faf7", font=_font(25, bold=True))
    draw.rounded_rectangle((1268, 74, 1488, 88), radius=7, fill=(255, 255, 255, 32))
    draw.rounded_rectangle(
        (1268, 74, 1268 + int(220 * (index + 1) / scene_count), 88),
        radius=7,
        fill=(*accent_rgb, 238),
    )

    if scene.source == "split":
        draw.rectangle((0, 0, WIDTH // 2, HEIGHT), fill=(0, 0, 0, 38))
        draw.rectangle((WIDTH // 2, 0, WIDTH, HEIGHT), fill=(80, 0, 0, 42))
        draw.line((WIDTH // 2, 130, WIDTH // 2, 626), fill=(*accent_rgb, 180), width=3)
        _small_tag(draw, (92, 148), "REFERENCE VIDEO / 真实运动底片", "#31d492")
        _small_tag(draw, (1032, 148), "FAILED SAMPLE / 运动但语义错位", "#ff6b6b")

    panel = (78, 666, 1842, 1012)
    draw.rounded_rectangle(panel, radius=24, fill=(2, 8, 8, 216), outline=(255, 255, 255, 48), width=1)
    draw.rectangle((panel[0], panel[1], panel[0] + 8, panel[3]), fill=(*accent_rgb, 255))

    title_font = _font(52, bold=True)
    body_font = _font(31)
    bullet_font = _font(29, bold=True)
    y = panel[1] + 34
    for line in _wrap_text_to_pixel_width(scene.title, title_font, 1180)[:2]:
        draw.text((panel[0] + 42, y), line, fill="#ffffff", font=title_font)
        y += 62
    y += 6
    for line in _wrap_text_to_pixel_width(scene.body, body_font, 1200)[:3]:
        draw.text((panel[0] + 44, y), line, fill="#dce7e0", font=body_font)
        y += 42

    bx, by = 1330, panel[1] + 46
    draw.text((bx, by - 6), "QUALITY CHECK", fill=scene.accent, font=_font(22, bold=True))
    by += 42
    for bullet in scene.bullets[:3]:
        draw.ellipse((bx, by, bx + 22, by + 22), fill=(*accent_rgb, 255))
        for line in _wrap_text_to_pixel_width(bullet, bullet_font, 390)[:2]:
            draw.text((bx + 38, by - 7), line, fill="#f8faf7", font=bullet_font)
            by += 35
        by += 24

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return output_path


def _build_single_source_scene_command(
    scene: MotionQualityLabScene,
    overlay_path: Path | str,
    output_path: Path | str,
) -> List[str]:
    source = _video_for_scene(scene)
    filter_complex = (
        f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS},"
        "eq=contrast=1.03:brightness=-0.025:saturation=1.02[bg];"
        "[1:v]format=rgba[ol];"
        "[bg][ol]overlay=0:0:format=auto,format=yuv420p[v];"
        "[0:a]volume=0.68,aformat=sample_rates=44100:channel_layouts=mono[a]"
    )
    return [
        "ffmpeg",
        "-y",
        "-ss",
        _format_time(scene.start),
        "-t",
        str(scene.duration),
        "-i",
        str(source),
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


def _build_split_scene_command(
    scene: MotionQualityLabScene,
    overlay_path: Path | str,
    output_path: Path | str,
) -> List[str]:
    failed_start = min(scene.start, 8)
    filter_complex = (
        f"[0:v]scale={WIDTH // 2}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH // 2}:{HEIGHT},setsar=1,fps={FPS},"
        "eq=contrast=1.03:brightness=-0.025:saturation=1.02[left];"
        f"[1:v]scale={WIDTH // 2}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH // 2}:{HEIGHT},setsar=1,fps={FPS},"
        "eq=contrast=1.04:brightness=-0.04:saturation=0.95[right];"
        "[left][right]hstack=inputs=2[bg];"
        "[2:v]format=rgba[ol];"
        "[bg][ol]overlay=0:0:format=auto,format=yuv420p[v];"
        "[0:a]volume=0.62,aformat=sample_rates=44100:channel_layouts=mono[a]"
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
        "-ss",
        _format_time(failed_start),
        "-t",
        str(scene.duration),
        "-i",
        str(FAILED_SAMPLE_VIDEO),
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


def _concat_segments(segment_paths: Sequence[Path], concat_path: Path, output_path: Path) -> None:
    lines = [f"file '{_escape_concat_path(path.resolve())}'" for path in segment_paths]
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
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
        [
            "ffmpeg",
            "-y",
            "-ss",
            "1",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-update",
            "1",
            str(cover_path),
        ],
        check=True,
    )


def _write_motion_script(scenes: Sequence[MotionQualityLabScene], output_path: Path) -> None:
    cursor = 0
    lines = ["# Codex 运动视频质量样片脚本", ""]
    for scene in scenes:
        lines.append(f"## {cursor:02d}-{cursor + scene.duration:02d}s  {scene.title}")
        lines.append(f"- Source: {scene.source}, start={scene.start}s")
        lines.append(scene.body)
        for bullet in scene.bullets:
            lines.append(f"- {bullet}")
        lines.append("")
        cursor += scene.duration
    output_path.write_text("\n".join(lines), encoding="utf-8")


def _write_motion_report(
    scenes: Sequence[MotionQualityLabScene],
    overlay_paths: Sequence[Path],
    segment_paths: Sequence[Path],
    release_path: Path,
    cover_path: Path,
    report_path: Path,
) -> None:
    payload = {
        "video": {
            "path": str(release_path),
            "width": WIDTH,
            "height": HEIGHT,
            "fps": FPS,
            "duration": motion_quality_lab_duration(scenes),
            "rendering_model": "trim real video clips, overlay transparent diagnostic captions, concatenate segments",
        },
        "sources": {
            "reference_video": str(SOURCE_VIDEO),
            "failed_sample_video": str(FAILED_SAMPLE_VIDEO),
        },
        "cover": str(cover_path),
        "scenes": [asdict(scene) for scene in scenes],
        "overlays": [str(path) for path in overlay_paths],
        "segments": [str(path) for path in segment_paths],
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _assert_source_videos() -> None:
    missing = [path for path in (SOURCE_VIDEO, FAILED_SAMPLE_VIDEO) if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing source video(s): " + ", ".join(str(path) for path in missing))


def _video_for_scene(scene: MotionQualityLabScene) -> Path:
    if scene.source == "reference":
        return SOURCE_VIDEO
    if scene.source == "failed":
        return FAILED_SAMPLE_VIDEO
    raise ValueError(f"Unsupported single-source scene: {scene.source}")


def _draw_vignette(image: Image.Image) -> None:
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    for y in range(520, HEIGHT, 8):
        alpha = min(205, int((y - 520) / (HEIGHT - 520) * 210))
        draw.rectangle((0, y, WIDTH, y + 8), fill=(0, 0, 0, alpha))
    draw.rectangle((0, 0, WIDTH, 132), fill=(0, 0, 0, 92))
    blurred = overlay.filter(ImageFilter.GaussianBlur(2))
    image.alpha_composite(blurred)


def _small_tag(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, color: str) -> None:
    x, y = xy
    width = int(_font(24, bold=True).getlength(text)) + 36
    draw.rounded_rectangle((x, y, x + width, y + 44), radius=16, fill=(0, 0, 0, 180), outline=(*_hex_to_rgb(color), 160), width=1)
    draw.text((x + 18, y + 9), text, fill=color, font=_font(24, bold=True))


def _format_time(seconds: float) -> str:
    if float(seconds).is_integer():
        return str(int(seconds))
    return f"{seconds:.3f}"


def _escape_concat_path(path: Path) -> str:
    return str(path).replace("'", r"'\''")


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))
