from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from video_factory import build_portugal_dr_congo_prediction_plan
from video_factory.pipeline import (
    Segment,
    _concat_segment_videos_with_audio,
    _extract_cover_frame,
    _font,
    _wrap_premium_title_text,
    _wrap_text_to_pixel_width,
)


SOURCE_VIDEO = Path("/Users/king/Downloads/下载.mp4")
OUTPUT_DIR = Path("video_factory/output/portugal-dr-congo-reference-clean")
WIDTH = 1920
HEIGHT = 1080
FPS = 30
SUBTITLE_LINE_CHARS = 28
SUBTITLE_CHUNK_CHARS = 50


def main() -> None:
    plan = build_portugal_dr_congo_prediction_plan()
    overlays_dir = OUTPUT_DIR / "overlays"
    subtitles_dir = OUTPUT_DIR / "subtitles"
    segments_dir = OUTPUT_DIR / "segments"
    overlays_dir.mkdir(parents=True, exist_ok=True)
    subtitles_dir.mkdir(parents=True, exist_ok=True)
    segments_dir.mkdir(parents=True, exist_ok=True)

    segment_videos: list[Path] = []
    for index, segment in enumerate(plan.segments):
        overlay_frames_dir = overlays_dir / f"frames_{index:02d}_{segment.role}"
        overlay_movie_path = overlays_dir / f"overlay_{index:02d}_{segment.role}.mov"
        segment_path = segments_dir / f"segment_{index:02d}_{segment.role}.mp4"

        progress = (index + 1) / len(plan.segments)
        _write_clean_overlay_movie(
            segment,
            index,
            len(plan.segments),
            progress,
            overlay_frames_dir,
            overlay_movie_path,
        )

        filter_complex = (
            f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS},"
            "eq=contrast=1.03:brightness=-0.025:saturation=1.02[bg];"
            "[bg][1:v]overlay=0:0:format=auto,format=yuv420p[v]"
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(segment.start),
                "-t",
                str(segment.duration),
                "-i",
                str(SOURCE_VIDEO),
                "-i",
                str(overlay_movie_path),
                "-filter_complex",
                filter_complex,
                "-t",
                str(segment.duration),
                "-map",
                "[v]",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(segment_path),
            ],
            check=True,
        )
        segment_videos.append(segment_path)

    voiceover = Path("video_factory/output/portugal-dr-congo-release/voiceover.wav")
    release = OUTPUT_DIR / "release.mp4"
    _concat_segment_videos_with_audio(segment_videos, voiceover, release, plan.config.target_duration)
    _extract_cover_frame(release, OUTPUT_DIR / "cover.png")


def _write_clean_overlay_movie(
    segment: Segment,
    index: int,
    segment_count: int,
    progress: float,
    frames_dir: Path,
    output_path: Path,
) -> None:
    frames_dir.mkdir(parents=True, exist_ok=True)
    for stale in frames_dir.glob("*"):
        stale.unlink()

    concat_path = frames_dir / "overlays.txt"
    entries: list[str] = []
    timed_chunks = _timed_subtitle_chunks(segment)
    last_frame: Path | None = None
    for chunk_index, (subtitle_text, duration) in enumerate(timed_chunks):
        frame_path = frames_dir / f"overlay_{chunk_index:03d}.png"
        _draw_clean_reference_overlay(
            segment,
            index,
            segment_count,
            progress,
            frame_path,
            subtitle_text=subtitle_text,
        )
        entries.append(f"file '{_escape_concat_path(frame_path.resolve())}'")
        entries.append(f"duration {duration:.4f}")
        last_frame = frame_path

    if last_frame is not None:
        entries.append(f"file '{_escape_concat_path(last_frame.resolve())}'")
    concat_path.write_text("\n".join(entries) + "\n", encoding="utf-8")

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
            "-vf",
            f"fps={FPS},format=rgba",
            "-c:v",
            "qtrle",
            str(output_path),
        ],
        check=True,
    )


def _draw_clean_reference_overlay(
    segment: Segment,
    index: int,
    segment_count: int,
    progress: float,
    output_path: Path,
    subtitle_text: str = "",
) -> None:
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Cover the original reference captions only where the new edit needs fresh copy.
    draw.rounded_rectangle((44, 34, 780, 266), radius=16, fill=(0, 0, 0, 188), outline=(80, 220, 160, 92), width=1)
    draw.text((66, 52), "WORLD CUP 26  |  GROUP K", fill="#cffff0", font=_font(23, bold=True))
    draw.text((610, 52), f"{index + 1}/{segment_count}", fill="#f5e84c", font=_font(23, bold=True))

    y = 98
    for line in _wrap_premium_title_text(segment.screen_text, 15)[:3]:
        draw.text((66, y), line, fill="#ffffff", font=_font(43, bold=True))
        y += 52

    draw.rounded_rectangle((66, 224, 350, 252), radius=14, fill=(28, 204, 128, 190))
    draw.text((86, 228), "预测 Portugal 2:1 DR Congo", fill="#04110d", font=_font(18, bold=True))

    draw.rounded_rectangle((44, 286, 780, 298), radius=6, fill=(12, 36, 30, 128))
    draw.rounded_rectangle((44, 286, 44 + int(736 * progress), 298), radius=6, fill=(245, 232, 76, 220))

    for row in range(820, HEIGHT, 8):
        alpha = min(220, 118 + int((row - 820) * 0.72))
        draw.rectangle((0, row, WIDTH, row + 8), fill=(0, 0, 0, alpha))
    draw.rectangle((0, 900, WIDTH, HEIGHT), fill=(0, 0, 0, 222))

    if subtitle_text:
        subtitle_font = _font(42, bold=True)
        subtitle_lines = _wrap_text_to_pixel_width(subtitle_text, subtitle_font, 1520)[:2]
        y = 904 if len(subtitle_lines) > 1 else 932
        for line in subtitle_lines:
            bbox = draw.textbbox((0, 0), line, font=subtitle_font, stroke_width=3)
            x = (WIDTH - (bbox[2] - bbox[0])) / 2
            draw.text(
                (x, y),
                line,
                fill="#ffffff",
                font=subtitle_font,
                stroke_width=3,
                stroke_fill=(0, 0, 0, 230),
            )
            y += 55

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def _timed_subtitle_chunks(segment: Segment) -> list[tuple[str, float]]:
    chunks = _subtitle_chunks(segment.narration)
    total_weight = sum(len(_plain_text(chunk)) for chunk in chunks) or 1
    timed: list[tuple[str, float]] = []
    assigned = 0.0
    for chunk in chunks[:-1]:
        duration = segment.duration * len(_plain_text(chunk)) / total_weight
        timed.append((chunk, duration))
        assigned += duration
    if chunks:
        timed.append((chunks[-1], max(0.1, segment.duration - assigned)))
    return timed


def _escape_concat_path(path: Path) -> str:
    return str(path).replace("'", r"'\''")


def _write_segment_ass(segment: Segment, output_path: Path) -> None:
    chunks = _subtitle_chunks(segment.narration)
    total_weight = sum(len(_plain_text(chunk)) for chunk in chunks) or 1
    cursor = 0.0
    events: list[str] = []

    for chunk in chunks:
        weight = max(1, len(_plain_text(chunk)))
        duration = segment.duration * weight / total_weight
        start = cursor
        end = min(float(segment.duration), cursor + duration)
        cursor = end
        ass_text = _format_ass_subtitle(chunk)
        events.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{ass_text}"
        )

    if events:
        last = events[-1].split(",", 3)
        last[2] = _ass_time(float(segment.duration))
        events[-1] = ",".join(last)

    content = "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            "PlayResX: 1920",
            "PlayResY: 1080",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            (
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
                "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                "Alignment, MarginL, MarginR, MarginV, Encoding"
            ),
            (
                "Style: Default,PingFang SC,42,&H00FFFFFF,&H000000FF,&H88000000,"
                "&HAA000000,-1,0,0,0,100,100,0,0,1,3,0,2,170,170,70,1"
            ),
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            *events,
            "",
        ]
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")


def _subtitle_chunks(text: str) -> list[str]:
    parts = [
        part.strip()
        for part in re.split(r"([。！？；])", text.replace("\n", " "))
        if part.strip()
    ]
    sentences: list[str] = []
    for index in range(0, len(parts), 2):
        sentence = parts[index]
        if index + 1 < len(parts):
            sentence += parts[index + 1]
        sentences.extend(_split_long_sentence(sentence))

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not current:
            current = sentence
            continue
        if len(_plain_text(current + sentence)) <= SUBTITLE_CHUNK_CHARS:
            current += sentence
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


def _split_long_sentence(sentence: str) -> list[str]:
    if len(_plain_text(sentence)) <= SUBTITLE_CHUNK_CHARS:
        return [sentence]
    pieces = [piece.strip() for piece in re.split(r"([，、：])", sentence) if piece.strip()]
    units: list[str] = []
    for index in range(0, len(pieces), 2):
        piece = pieces[index]
        if index + 1 < len(pieces):
            piece += pieces[index + 1]
        units.append(piece)

    result: list[str] = []
    current = ""
    for unit in units:
        if len(_plain_text(unit)) > SUBTITLE_CHUNK_CHARS:
            if current:
                result.append(current)
                current = ""
            result.extend(_hard_wrap(unit, SUBTITLE_CHUNK_CHARS))
        elif len(_plain_text(current + unit)) <= SUBTITLE_CHUNK_CHARS:
            current += unit
        else:
            if current:
                result.append(current)
            current = unit
    if current:
        result.append(current)
    return result


def _hard_wrap(text: str, limit: int) -> list[str]:
    return [text[index : index + limit] for index in range(0, len(text), limit)]


def _format_ass_subtitle(text: str) -> str:
    lines = _hard_wrap(text, SUBTITLE_LINE_CHARS)
    if len(lines) > 2:
        first = lines[0]
        second = "".join(lines[1:])
        lines = [first, second[:SUBTITLE_LINE_CHARS]]
    escaped = [line.replace("{", "").replace("}", "") for line in lines[:2]]
    return r"\N".join(escaped)


def _plain_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centiseconds = int(round((seconds - int(seconds)) * 100))
    if centiseconds >= 100:
        secs += 1
        centiseconds -= 100
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


if __name__ == "__main__":
    main()
