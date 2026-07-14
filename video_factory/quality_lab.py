from __future__ import annotations

import json
import math
import shutil
import subprocess
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Sequence

from PIL import Image, ImageDraw, ImageFilter

from video_factory.pipeline import _font, _wrap_text_to_pixel_width, build_ffmpeg_command


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WIDTH = 1920
HEIGHT = 1080
FPS = 30
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "video_factory/output/quality-lab-sample"
REFERENCE_ANALYSIS_DIR = PROJECT_ROOT / "video_factory/output/analysis/download-reference"
FAILED_SAMPLE_VIDEO = PROJECT_ROOT / "video_factory/output/portugal-dr-congo-reference-clean/release.mp4"


@dataclass(frozen=True)
class QualityLabScene:
    key: str
    duration: int
    title: str
    eyebrow: str
    body: str
    bullets: Sequence[str]
    evidence: str
    accent: str


@dataclass(frozen=True)
class QualityLabEvidence:
    reference_contact_sheet: Path
    failed_contact_sheet: Path
    reference_frames: Sequence[Path]


def build_quality_lab_storyboard() -> List[QualityLabScene]:
    return [
        QualityLabScene(
            key="hook",
            duration=8,
            title="问题不在清晰度，在可信链",
            eyebrow="QUALITY GAP",
            body="同样是 1080P/4K，观众一眼会分出真假。差距来自画面、字幕、主题、证据是否指向同一件事。",
            bullets=("原片有真实屏幕和人物锚点", "失败样片是新主题贴在旧画面上", "先修质量链，再谈批量生产"),
            evidence="split",
            accent="#31d492",
        ),
        QualityLabScene(
            key="reference_anchor",
            duration=10,
            title="原片的第一层真实感：锚点",
            eyebrow="REFERENCE",
            body="你给的视频不是靠炫技赢，而是靠真实操作界面、人物、字幕和讲解节奏形成统一语境。",
            bullets=("画面内容能解释字幕", "镜头不是随机素材", "每一屏都服务同一主题"),
            evidence="reference_frame_0",
            accent="#72e4ff",
        ),
        QualityLabScene(
            key="reference_structure",
            duration=10,
            title="第二层真实感：信息结构",
            eyebrow="STRUCTURE",
            body="画面里反复出现 agent、knowledge base、framework answer 等信息块，观众能看见“过程”。",
            bullets=("不是只有包装", "屏幕证据推动叙事", "字幕不抢画面主体"),
            evidence="reference_frame_2",
            accent="#f0c95a",
        ),
        QualityLabScene(
            key="failure",
            duration=12,
            title="上一版为什么还是 AI 味重",
            eyebrow="FAILURE MODE",
            body="它用了原视频当底，但上面硬贴葡萄牙比分。观众看到的是两套世界观：底片在讲 Codex，字幕在讲足球。",
            bullets=("主题不同源", "比分贴片没有比赛证据", "字幕、底片、声音没有共同目标"),
            evidence="failed",
            accent="#ff6b6b",
        ),
        QualityLabScene(
            key="scorecard",
            duration=11,
            title="质量评估要从这五项打分",
            eyebrow="SCORECARD",
            body="以后每条视频先过质量门槛。没有同源素材，就不要进入最终渲染。",
            bullets=("语义一致性", "真实素材密度", "字幕与声音同步", "包装克制", "节奏推进"),
            evidence="scorecard",
            accent="#a78bfa",
        ),
        QualityLabScene(
            key="workflow",
            duration=15,
            title="正确流水线：先素材包，再生成",
            eyebrow="PRODUCTION FLOW",
            body="Codex 适合做批量工程：拆参考、建模板、收素材、排镜头、合成、质检。最关键的是素材阶段不能偷懒。",
            bullets=("参考拆解生成规则", "同主题素材包入库", "自动剪辑后逐段质检"),
            evidence="workflow",
            accent="#31d492",
        ),
        QualityLabScene(
            key="close",
            duration=12,
            title="下一步：做同源样片",
            eyebrow="NEXT SAMPLE",
            body="这条样片先把质量问题说清楚。下一条必须换成同主题真实素材：如果讲比赛，就要用合规足球素材；如果讲 Codex，就继续用产品演示素材。",
            bullets=("不再硬换主题", "不再用假画面撑真实感", "每个镜头都能回答：我在证明什么"),
            evidence="final",
            accent="#f0c95a",
        ),
    ]


def quality_lab_duration(scenes: Sequence[QualityLabScene]) -> int:
    return sum(scene.duration for scene in scenes)


def build_quality_lab_ffmpeg_command(
    frames_file: Path | str,
    audio_file: Path | str,
    output_file: Path | str,
    duration: int,
) -> List[str]:
    return build_ffmpeg_command(
        frames_file,
        audio_file,
        output_file,
        duration=duration,
        fps=FPS,
        width=WIDTH,
        height=HEIGHT,
    )


def render_quality_lab_sample(output_dir: Path = DEFAULT_OUTPUT_DIR) -> Dict[str, Path]:
    output_dir = Path(output_dir)
    frames_dir = output_dir / "frames"
    evidence_dir = output_dir / "evidence"
    frames_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    scenes = build_quality_lab_storyboard()
    evidence = prepare_quality_lab_evidence(evidence_dir)
    frames = render_quality_lab_frames(scenes, frames_dir, evidence)
    concat_path = output_dir / "frames.txt"
    audio_path = output_dir / "voiceover.wav"
    video_path = output_dir / "release.mp4"
    cover_path = output_dir / "cover.png"
    script_path = output_dir / "script.md"
    report_path = output_dir / "render_report.json"

    write_quality_lab_concat(frames, scenes, concat_path)
    build_quality_lab_tone_track(scenes, audio_path)
    subprocess.run(
        build_quality_lab_ffmpeg_command(
            concat_path,
            audio_path,
            video_path,
            duration=quality_lab_duration(scenes),
        ),
        check=True,
    )
    shutil.copyfile(frames[0], cover_path)
    write_quality_lab_script(scenes, script_path)
    write_quality_lab_report(scenes, evidence, frames, video_path, cover_path, report_path)

    return {
        "video": video_path,
        "cover": cover_path,
        "script": script_path,
        "report": report_path,
        "audio": audio_path,
        "concat": concat_path,
    }


def prepare_quality_lab_evidence(evidence_dir: Path) -> QualityLabEvidence:
    reference_contact = REFERENCE_ANALYSIS_DIR / "contact_sheet.jpg"
    reference_frames = sorted((REFERENCE_ANALYSIS_DIR / "sample_frames").glob("*.jpg"))
    if not reference_contact.exists() or not reference_frames:
        raise FileNotFoundError(
            "Reference analysis artifacts are missing. Run "
            "`python3 -m video_factory.analysis --input /Users/king/Downloads/下载.mp4 "
            "--output video_factory/output/analysis/download-reference --sample-count 8` first."
        )

    failed_contact = evidence_dir / "failed_sample_contact_sheet.jpg"
    _write_failed_sample_contact_sheet(failed_contact)
    return QualityLabEvidence(
        reference_contact_sheet=reference_contact,
        failed_contact_sheet=failed_contact,
        reference_frames=reference_frames,
    )


def render_quality_lab_frames(
    scenes: Sequence[QualityLabScene],
    frames_dir: Path,
    evidence: QualityLabEvidence,
) -> List[Path]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    for stale in frames_dir.glob("*.png"):
        stale.unlink()

    frame_paths: List[Path] = []
    for index, scene in enumerate(scenes):
        frame_path = frames_dir / f"{index:02d}_{scene.key}.png"
        image = _draw_quality_lab_scene(scene, index, len(scenes), evidence)
        image.save(frame_path)
        frame_paths.append(frame_path)
    return frame_paths


def write_quality_lab_concat(
    frames: Sequence[Path],
    scenes: Sequence[QualityLabScene],
    concat_path: Path,
) -> None:
    lines: List[str] = []
    for frame, scene in zip(frames, scenes):
        lines.append(f"file '{_escape_concat_path(frame.resolve())}'")
        lines.append(f"duration {scene.duration}")
    if frames:
        lines.append(f"file '{_escape_concat_path(frames[-1].resolve())}'")
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_quality_lab_tone_track(
    scenes: Sequence[QualityLabScene],
    output_path: Path,
    sample_rate: int = 44100,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = quality_lab_duration(scenes)
    starts: List[float] = []
    cursor = 0.0
    for scene in scenes:
        starts.append(cursor)
        cursor += scene.duration

    frames = bytearray()
    total_samples = duration * sample_rate
    for sample_index in range(total_samples):
        t = sample_index / sample_rate
        value = 0.025 * math.sin(2 * math.pi * 92 * t)
        value += 0.015 * math.sin(2 * math.pi * 184 * t)
        for start in starts:
            offset = t - start
            if 0 <= offset < 0.28:
                envelope = 1.0 - offset / 0.28
                value += 0.18 * envelope * math.sin(2 * math.pi * 620 * offset)
        int_sample = int(max(-1.0, min(1.0, value)) * 32767)
        frames.extend(int_sample.to_bytes(2, byteorder="little", signed=True))

    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(bytes(frames))
    return output_path


def write_quality_lab_script(scenes: Sequence[QualityLabScene], output_path: Path) -> None:
    lines = ["# Codex 视频质量拆解样片脚本", ""]
    cursor = 0
    for scene in scenes:
        lines.append(f"## {cursor:02d}-{cursor + scene.duration:02d}s  {scene.title}")
        lines.append(scene.body)
        for bullet in scene.bullets:
            lines.append(f"- {bullet}")
        lines.append("")
        cursor += scene.duration
    output_path.write_text("\n".join(lines), encoding="utf-8")


def write_quality_lab_report(
    scenes: Sequence[QualityLabScene],
    evidence: QualityLabEvidence,
    frames: Sequence[Path],
    video_path: Path,
    cover_path: Path,
    output_path: Path,
) -> None:
    payload = {
        "video": {
            "path": str(video_path),
            "width": WIDTH,
            "height": HEIGHT,
            "fps": FPS,
            "duration": quality_lab_duration(scenes),
        },
        "cover": str(cover_path),
        "scenes": [asdict(scene) for scene in scenes],
        "evidence": {
            "reference_contact_sheet": str(evidence.reference_contact_sheet),
            "failed_contact_sheet": str(evidence.failed_contact_sheet),
            "reference_frames": [str(path) for path in evidence.reference_frames],
        },
        "frames": [str(path) for path in frames],
        "note": "字幕版质量拆解样片；音频为节奏参考轨，正式版应接入真实旁白或合规 TTS。",
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _draw_quality_lab_scene(
    scene: QualityLabScene,
    index: int,
    scene_count: int,
    evidence: QualityLabEvidence,
) -> Image.Image:
    image = _background(scene.accent)
    draw = ImageDraw.Draw(image, "RGBA")

    _draw_top_bar(draw, scene, index, scene_count)
    _draw_evidence_area(image, draw, scene, evidence)
    _draw_text_stack(draw, scene)
    _draw_footer(draw, index, scene_count)
    return image.convert("RGB")


def _background(accent: str) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#090d0d")
    draw = ImageDraw.Draw(image)
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        red = int(9 + ratio * 24)
        green = int(13 + ratio * 18)
        blue = int(13 + ratio * 10)
        draw.line((0, y, WIDTH, y), fill=(red, green, blue))

    glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow, "RGBA")
    rgb = _hex_to_rgb(accent)
    glow_draw.ellipse((-280, -260, 780, 720), fill=(*rgb, 56))
    glow_draw.ellipse((1200, 580, 2200, 1400), fill=(240, 201, 90, 36))
    glow = glow.filter(ImageFilter.GaussianBlur(82))
    return Image.alpha_composite(image.convert("RGBA"), glow).convert("RGB")


def _draw_top_bar(
    draw: ImageDraw.ImageDraw,
    scene: QualityLabScene,
    index: int,
    scene_count: int,
) -> None:
    draw.rounded_rectangle((72, 52, 1848, 112), radius=18, fill=(5, 12, 12, 162), outline=(255, 255, 255, 36), width=1)
    draw.text((100, 70), "CODEX VIDEO QUALITY LAB", fill="#f5f7f2", font=_font(22, bold=True))
    draw.text((472, 70), scene.eyebrow, fill=scene.accent, font=_font(22, bold=True))
    progress_x = 1540
    draw.rounded_rectangle((progress_x, 76, 1790, 90), radius=7, fill=(255, 255, 255, 30))
    draw.rounded_rectangle(
        (progress_x, 76, progress_x + int(250 * (index + 1) / scene_count), 90),
        radius=7,
        fill=scene.accent,
    )
    draw.text((1810, 66), f"{index + 1}/{scene_count}", fill="#f5f7f2", font=_font(26, bold=True))


def _draw_text_stack(draw: ImageDraw.ImageDraw, scene: QualityLabScene) -> None:
    x0, y0, x1, y1 = 92, 650, 1828, 1014
    draw.rounded_rectangle((x0, y0, x1, y1), radius=26, fill=(6, 11, 11, 214), outline=(255, 255, 255, 44), width=1)
    draw.rectangle((x0, y0, x0 + 8, y1), fill=scene.accent)

    title_font = _font(54, bold=True)
    body_font = _font(31)
    bullet_font = _font(30, bold=True)
    y = y0 + 34
    for line in _wrap_text_to_pixel_width(scene.title, title_font, 1040)[:2]:
        draw.text((x0 + 40, y), line, fill="#ffffff", font=title_font)
        y += 65

    y += 8
    body_lines = _wrap_text_to_pixel_width(scene.body, body_font, 1180)[:3]
    for line in body_lines:
        draw.text((x0 + 42, y), line, fill="#dbe7e0", font=body_font)
        y += 43

    bullet_x = 1290
    bullet_y = y0 + 48
    draw.text((bullet_x, bullet_y - 4), "QUALITY RULES", fill=scene.accent, font=_font(24, bold=True))
    bullet_y += 44
    for bullet in scene.bullets[:4]:
        draw.rounded_rectangle((bullet_x, bullet_y, bullet_x + 24, bullet_y + 24), radius=12, fill=scene.accent)
        for line in _wrap_text_to_pixel_width(bullet, bullet_font, 430)[:2]:
            draw.text((bullet_x + 42, bullet_y - 6), line, fill="#f5f7f2", font=bullet_font)
            bullet_y += 35
        bullet_y += 22


def _draw_evidence_area(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    scene: QualityLabScene,
    evidence: QualityLabEvidence,
) -> None:
    if scene.evidence == "split":
        _draw_split_evidence(image, draw, evidence, scene.accent)
    elif scene.evidence == "failed":
        _draw_failed_evidence(image, draw, evidence.failed_contact_sheet, scene.accent)
    elif scene.evidence == "scorecard":
        _draw_scorecard(draw, scene.accent)
    elif scene.evidence == "workflow":
        _draw_workflow(draw, scene.accent)
    elif scene.evidence == "final":
        _draw_final_pillars(draw, scene.accent)
    else:
        frame_index = 0 if scene.evidence.endswith("_0") else min(2, len(evidence.reference_frames) - 1)
        _draw_reference_frame(image, draw, evidence.reference_frames[frame_index], scene.accent)


def _draw_split_evidence(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    evidence: QualityLabEvidence,
    accent: str,
) -> None:
    left_box = (92, 154, 934, 600)
    right_box = (986, 154, 1828, 600)
    _paste_card_image(image, evidence.reference_contact_sheet, left_box)
    _paste_card_image(image, evidence.failed_contact_sheet, right_box)
    _label(draw, left_box, "REFERENCE: 同一主题证据链", "#31d492")
    _label(draw, right_box, "FAILED SAMPLE: 主题硬贴", "#ff6b6b")
    draw.line((960, 188, 960, 566), fill=(*_hex_to_rgb(accent), 140), width=2)
    draw.text((907, 350), "VS", fill="#ffffff", font=_font(48, bold=True))


def _draw_reference_frame(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    frame_path: Path,
    accent: str,
) -> None:
    box = (92, 150, 1254, 602)
    _paste_card_image(image, frame_path, box)
    _label(draw, box, "原片截图：画面证据与字幕同源", accent)
    callouts = [
        ((1350, 166, 1768, 236), "真实操作界面"),
        ((1350, 272, 1768, 342), "人物/屏幕锚点"),
        ((1350, 378, 1768, 448), "信息块推动叙事"),
        ((1350, 484, 1768, 554), "字幕不遮挡主体"),
    ]
    for rect, text in callouts:
        draw.rounded_rectangle(rect, radius=16, fill=(255, 255, 255, 18), outline=(*_hex_to_rgb(accent), 128), width=2)
        draw.text((rect[0] + 24, rect[1] + 17), text, fill="#f5f7f2", font=_font(29, bold=True))


def _draw_failed_evidence(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    failed_contact: Path,
    accent: str,
) -> None:
    box = (92, 150, 1200, 602)
    _paste_card_image(image, failed_contact, box)
    _label(draw, box, "上一版：底片和主题互相打架", accent)
    x0, y0 = 1270, 160
    rows = [
        ("底片", "Codex/AI 教学画面"),
        ("字幕", "葡萄牙 vs 刚果比分"),
        ("证据", "没有比赛同源素材"),
        ("结果", "观众感到“假”"),
    ]
    for idx, (label, value) in enumerate(rows):
        y = y0 + idx * 100
        draw.rounded_rectangle((x0, y, 1818, y + 72), radius=18, fill=(255, 255, 255, 18), outline=(255, 107, 107, 118), width=1)
        draw.text((x0 + 24, y + 18), label, fill=accent, font=_font(26, bold=True))
        draw.text((x0 + 118, y + 18), value, fill="#ffffff", font=_font(28, bold=True))


def _draw_scorecard(draw: ImageDraw.ImageDraw, accent: str) -> None:
    x0, y0 = 150, 156
    metrics = [
        ("语义一致性", 28),
        ("真实素材密度", 35),
        ("字幕/声音匹配", 42),
        ("包装克制", 54),
        ("节奏推进", 58),
    ]
    draw.text((x0, y0), "FAILED SAMPLE SCORE", fill="#ffffff", font=_font(42, bold=True))
    draw.text((1370, 178), "39/100", fill=accent, font=_font(92, bold=True))
    for idx, (label, score) in enumerate(metrics):
        y = y0 + 88 + idx * 72
        draw.text((x0, y - 8), label, fill="#f5f7f2", font=_font(29, bold=True))
        draw.rounded_rectangle((430, y, 1540, y + 24), radius=12, fill=(255, 255, 255, 28))
        draw.rounded_rectangle((430, y, 430 + int(1110 * score / 100), y + 24), radius=12, fill=accent)
        draw.text((1580, y - 12), f"{score}", fill="#ffffff", font=_font(30, bold=True))


def _draw_workflow(draw: ImageDraw.ImageDraw, accent: str) -> None:
    steps = [
        ("01", "拆参考", "镜头 / 字幕 / 音频 / 视觉系统"),
        ("02", "收素材", "必须同主题、可商用、能证明脚本"),
        ("03", "排镜头", "先证据，后包装，不硬贴标签"),
        ("04", "合成", "字幕、旁白、画面统一节奏"),
        ("05", "质检", "逐段打分，低于门槛重做"),
    ]
    x = 104
    for idx, (num, title, detail) in enumerate(steps):
        box = (x + idx * 352, 170, x + idx * 352 + 304, 574)
        draw.rounded_rectangle(box, radius=24, fill=(255, 255, 255, 20), outline=(*_hex_to_rgb(accent), 98), width=2)
        draw.text((box[0] + 28, box[1] + 26), num, fill=accent, font=_font(34, bold=True))
        draw.text((box[0] + 28, box[1] + 92), title, fill="#ffffff", font=_font(42, bold=True))
        y = box[1] + 170
        for line in _wrap_text_to_pixel_width(detail, _font(25), 240)[:4]:
            draw.text((box[0] + 28, y), line, fill="#dbe7e0", font=_font(25))
            y += 34
        if idx < len(steps) - 1:
            draw.line((box[2] + 18, 372, box[2] + 48, 372), fill=(255, 255, 255, 94), width=3)


def _draw_final_pillars(draw: ImageDraw.ImageDraw, accent: str) -> None:
    pillars = [
        ("同源素材", "讲什么，就出现什么证据"),
        ("真实节奏", "镜头变化服务信息推进"),
        ("批量质检", "模板只负责稳定，不负责掩盖问题"),
    ]
    for idx, (title, detail) in enumerate(pillars):
        x0 = 156 + idx * 570
        draw.rounded_rectangle((x0, 172, x0 + 480, 570), radius=28, fill=(255, 255, 255, 22), outline=(*_hex_to_rgb(accent), 112), width=2)
        draw.text((x0 + 44, 222), f"0{idx + 1}", fill=accent, font=_font(46, bold=True))
        draw.text((x0 + 44, 312), title, fill="#ffffff", font=_font(48, bold=True))
        y = 404
        for line in _wrap_text_to_pixel_width(detail, _font(28), 360)[:3]:
            draw.text((x0 + 44, y), line, fill="#dbe7e0", font=_font(28))
            y += 38


def _draw_footer(draw: ImageDraw.ImageDraw, index: int, scene_count: int) -> None:
    draw.text((94, 1032), "sample: quality-lab-sample / subtitle diagnostic cut", fill="#8da39a", font=_font(20))
    draw.text((1618, 1032), f"scene {index + 1:02d} of {scene_count:02d}", fill="#8da39a", font=_font(20))


def _paste_card_image(image: Image.Image, source_path: Path, box: tuple[int, int, int, int]) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0 - 3, y0 - 3, x1 + 3, y1 + 3), radius=26, fill=(255, 255, 255, 24))
    with Image.open(source_path) as source:
        fitted = _cover_image(source.convert("RGB"), (x1 - x0, y1 - y0))
    image.paste(fitted, (x0, y0))
    overlay = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay, "RGBA")
    overlay_draw.rectangle((0, y1 - y0 - 92, x1 - x0, y1 - y0), fill=(0, 0, 0, 132))
    image.alpha_composite(overlay, (x0, y0)) if image.mode == "RGBA" else image.paste(Image.alpha_composite(image.crop(box).convert("RGBA"), overlay).convert("RGB"), (x0, y0))


def _label(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, color: str) -> None:
    x0, _y0, _x1, y1 = box
    draw.text((x0 + 24, y1 - 68), text, fill=color, font=_font(27, bold=True))


def _cover_image(source: Image.Image, size: tuple[int, int]) -> Image.Image:
    target_w, target_h = size
    ratio = max(target_w / source.width, target_h / source.height)
    resized = source.resize((int(source.width * ratio), int(source.height * ratio)), Image.Resampling.LANCZOS)
    left = max(0, (resized.width - target_w) // 2)
    top = max(0, (resized.height - target_h) // 2)
    return resized.crop((left, top, left + target_w, top + target_h))


def _write_failed_sample_contact_sheet(output_path: Path) -> None:
    if output_path.exists():
        return
    if FAILED_SAMPLE_VIDEO.exists():
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(FAILED_SAMPLE_VIDEO),
                    "-vf",
                    "fps=1/55,scale=640:-1,tile=3x2",
                    "-frames:v",
                    "1",
                    str(output_path),
                ],
                check=True,
            )
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
    _write_missing_evidence_card(output_path, "FAILED SAMPLE NOT FOUND")


def _write_missing_evidence_card(output_path: Path, text: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1280, 720), "#171717")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((70, 70, 1210, 650), radius=28, outline="#ff6b6b", width=4)
    draw.text((140, 300), text, fill="#ff6b6b", font=_font(58, bold=True))
    image.save(output_path)


def _escape_concat_path(path: Path) -> str:
    return str(path).replace("'", r"'\''")


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))
