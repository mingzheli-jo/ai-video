from __future__ import annotations

import sys
import json
import math
import re
import subprocess
import tempfile
from array import array
from pathlib import Path
from typing import Any

from PIL import Image


def text_overlap_ratio(source_text: str, output_text: str) -> float | None:
    source_tokens = _text_tokens(source_text)
    output_tokens = _text_tokens(output_text)
    if not source_tokens or not output_tokens:
        return None
    intersection = source_tokens & output_tokens
    return round(2 * len(intersection) / (len(source_tokens) + len(output_tokens)), 4)


def parse_edl_source_reuse(edl_path: Path | str, output_duration: float) -> dict[str, float | int]:
    path = Path(edl_path)
    if not path.exists() or output_duration <= 0:
        return {"source_segment_duration": 0.0, "source_reuse_ratio": 0.0, "segment_count": 0}
    total = 0.0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\|\s*\d+\s*\|\s*\d+:\d+\s*\|\s*(\d+):(\d+)\s*\|", line)
        if not match:
            continue
        total += int(match.group(1)) * 60 + int(match.group(2))
        count += 1
    return {
        "source_segment_duration": round(total, 3),
        "source_reuse_ratio": round(min(1.0, total / output_duration), 4),
        "segment_count": count,
    }


def classify_originality_risk(
    visual_similarity: float | None,
    audio_reuse_ratio: float | None,
    text_overlap_ratio: float | None,
    source_reuse_ratio: float | None,
    duration_retention: float | None,
) -> dict[str, Any]:
    metrics = {
        "visual_similarity": visual_similarity,
        "audio_reuse_ratio": audio_reuse_ratio,
        "text_overlap_ratio": text_overlap_ratio,
        "source_reuse_ratio": source_reuse_ratio,
        "duration_retention": duration_retention,
    }
    weights = {
        "visual_similarity": 0.28,
        "audio_reuse_ratio": 0.24,
        "text_overlap_ratio": 0.18,
        "source_reuse_ratio": 0.22,
        "duration_retention": 0.08,
    }
    available_weight = sum(weights[key] for key, value in metrics.items() if value is not None)
    if available_weight <= 0:
        score = 0
    else:
        score = round(
            sum((metrics[key] or 0.0) * weights[key] for key in metrics if metrics[key] is not None)
            / available_weight
            * 100
        )
    if (
        (source_reuse_ratio is not None and source_reuse_ratio >= 0.70 and visual_similarity is not None and visual_similarity >= 0.72)
        or (audio_reuse_ratio is not None and audio_reuse_ratio >= 0.75 and visual_similarity is not None and visual_similarity >= 0.65)
        or score >= 72
    ):
        risk_level = "high"
        risk_reason = "高度同源：主体画面、声音或片段结构与参考视频重合度过高。"
    elif score >= 45:
        risk_level = "medium"
        risk_reason = "中等同源：存在明显源片复用，需要提高原创画面、解说或结构比例。"
    else:
        risk_level = "low"
        risk_reason = "低同源：本地指标未发现明显重复风险，仍需人工确认授权和平台规则。"
    return {
        "similarity_score": score,
        "risk_level": risk_level,
        "risk_reason": risk_reason,
        "available_metrics": [key for key, value in metrics.items() if value is not None],
    }


def build_originality_recommendations(report: dict[str, Any]) -> list[str]:
    risk = str(report.get("risk_level", "unknown"))
    visual = float(report.get("visual_similarity") or 0.0)
    audio = float(report.get("audio_reuse_ratio") or 0.0)
    text = float(report.get("text_overlap_ratio") or 0.0)
    source_reuse = float(report.get("source_reuse_ratio") or 0.0)
    recommendations: list[str] = []
    if risk == "high":
        recommendations.append("不要以重剪版直接发布，先提高原创内容比例。")
    if source_reuse >= 0.45 or visual >= 0.65:
        recommendations.append("增加自有画面或重新录屏，把源片直接画面复用控制在较低比例。")
    if audio >= 0.55:
        recommendations.append("使用原创解说或现场声音，不要整段保留原片音频。")
    if text >= 0.45:
        recommendations.append("重写脚本结构和表达方式，避免沿用原文案顺序。")
    if visual >= 0.65 and source_reuse >= 0.45:
        recommendations.append("把原片改为参考资料，只保留必要短引用，并加入清晰评论、分析或教学增量。")
    return recommendations or ["当前重复风险较低，发布前仍需确认素材授权、引用边界和平台规则。"]


def write_originality_report(output_path: Path | str, metrics: dict[str, Any]) -> dict[str, Any]:
    report = _compose_report(metrics)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def build_originality_report(
    source_video: Path | str,
    output_video: Path | str,
    output_path: Path | str,
    edl_path: Path | str | None = None,
    max_samples: int = 18,
) -> dict[str, Any]:
    source = Path(source_video)
    output = Path(output_video)
    source_duration = _probe_duration(source)
    output_duration = _probe_duration(output)
    duration_retention = _safe_ratio(output_duration, source_duration)
    source_reuse = parse_edl_source_reuse(edl_path, output_duration) if edl_path else {}
    source_reuse_ratio = source_reuse.get("source_reuse_ratio") if source_reuse else None
    visual_similarity = _estimate_visual_similarity(source, output, source_duration, output_duration, edl_path, max_samples)
    audio_reuse_ratio = _estimate_direct_audio_reuse(source, output)
    audio_reuse_provider = "audio_fingerprint"
    if audio_reuse_ratio is None and source_reuse_ratio is not None:
        audio_reuse_ratio = source_reuse_ratio
        audio_reuse_provider = "edl_source_reuse_fallback"
    source_text = _read_sidecar_text(source)
    output_text = _read_sidecar_text(output)
    text_ratio = text_overlap_ratio(source_text, output_text)
    text_provider = "sidecar"
    if text_ratio is None and audio_reuse_ratio is not None:
        text_ratio = round(audio_reuse_ratio, 4)
        text_provider = "estimated_from_audio_reuse"
    report = _compose_report(
        {
            "visual_similarity": visual_similarity,
            "audio_reuse_ratio": audio_reuse_ratio,
            "audio_reuse_provider": audio_reuse_provider,
            "text_overlap_ratio": text_ratio,
            "text_overlap_provider": text_provider,
            "source_reuse_ratio": source_reuse_ratio,
            "duration_retention": duration_retention,
            "source_duration": source_duration,
            "output_duration": output_duration,
            "source_reuse": source_reuse,
        }
    )
    path = Path(output_path)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _compose_report(metrics: dict[str, Any]) -> dict[str, Any]:
    risk = classify_originality_risk(
        metrics.get("visual_similarity"),
        metrics.get("audio_reuse_ratio"),
        metrics.get("text_overlap_ratio"),
        metrics.get("source_reuse_ratio"),
        metrics.get("duration_retention"),
    )
    flat = {
        "risk_level": risk["risk_level"],
        "risk_reason": risk["risk_reason"],
        "similarity_score": risk["similarity_score"],
        "visual_similarity": metrics.get("visual_similarity"),
        "audio_reuse_ratio": metrics.get("audio_reuse_ratio"),
        "text_overlap_ratio": metrics.get("text_overlap_ratio"),
        "source_reuse_ratio": metrics.get("source_reuse_ratio"),
    }
    recommendations = build_originality_recommendations(flat)
    return {
        "risk_level": risk["risk_level"],
        "risk_reason": risk["risk_reason"],
        "similarity_score": risk["similarity_score"],
        "metrics": {
            "visual_similarity": _round_optional(metrics.get("visual_similarity")),
            "audio_reuse_ratio": _round_optional(metrics.get("audio_reuse_ratio")),
            "audio_reuse_provider": metrics.get("audio_reuse_provider", "provided"),
            "text_overlap_ratio": _round_optional(metrics.get("text_overlap_ratio")),
            "text_overlap_provider": metrics.get("text_overlap_provider", "provided"),
            "source_reuse_ratio": _round_optional(metrics.get("source_reuse_ratio")),
            "duration_retention": _round_optional(metrics.get("duration_retention")),
            "source_duration": _round_optional(metrics.get("source_duration")),
            "output_duration": _round_optional(metrics.get("output_duration")),
        },
        "source_reuse": metrics.get("source_reuse", {}),
        "recommendations": recommendations,
        "disclaimer": "本报告是本地可解释原创度估算，不代表任何平台的真实审核结果，也不用于规避平台检测。",
    }


def _text_tokens(text: str) -> set[str]:
    normalized = re.sub(r"\s+", " ", text.lower())
    words = set(re.findall(r"[a-z0-9_]+", normalized))
    chinese_chars = set(re.findall(r"[\u4e00-\u9fff]", normalized))
    return {token for token in words | chinese_chars if token}


def _probe_duration(path: Path) -> float | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(path)],
            check=True,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
        return round(float(result.stdout.strip()), 3)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def _estimate_visual_similarity(
    source: Path,
    output: Path,
    source_duration: float | None,
    output_duration: float | None,
    edl_path: Path | str | None,
    max_samples: int,
) -> float | None:
    if not source_duration or not output_duration:
        return None
    pairs = _aligned_sample_pairs(edl_path, output_duration, max_samples)
    if not pairs:
        pairs = [(t, min(t, source_duration - 0.05)) for t in _uniform_timestamps(output_duration, max_samples)]
    tmp = Path(tempfile.mkdtemp(prefix="vf_originality_", dir="/private/tmp" if sys.platform == "darwin" else None))
    scores: list[float] = []
    for index, (output_time, source_time) in enumerate(pairs[:max_samples]):
        source_frame = tmp / f"source_{index:02d}.jpg"
        output_frame = tmp / f"output_{index:02d}.jpg"
        if not (_extract_frame(source, source_time, source_frame) and _extract_frame(output, output_time, output_frame)):
            continue
        scores.append(_hash_similarity(_dhash(source_frame), _dhash(output_frame)))
    if not scores:
        return None
    return round(sum(scores) / len(scores), 4)


def _estimate_direct_audio_reuse(source: Path, output: Path) -> float | None:
    tmp = Path(tempfile.mkdtemp(prefix="vf_originality_audio_", dir="/private/tmp" if sys.platform == "darwin" else None))
    source_audio = tmp / "source.raw"
    output_audio = tmp / "output.raw"
    if not (_extract_audio_pcm(source, source_audio) and _extract_audio_pcm(output, output_audio)):
        return None
    source_series = _audio_energy_series(source_audio)
    output_series = _audio_energy_series(output_audio)
    if not source_series or not output_series:
        return None
    return round(_series_similarity(source_series, output_series), 4)


def _extract_audio_pcm(video: Path, output: Path, limit_seconds: int = 120) -> bool:
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(video),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "8000",
                "-t",
                str(limit_seconds),
                "-f",
                "s16le",
                str(output),
            ],
            check=True,
        )
        return output.exists() and output.stat().st_size > 0
    except (OSError, subprocess.CalledProcessError):
        return False


def _audio_energy_series(raw_path: Path, sample_rate: int = 8000, window_seconds: float = 0.25) -> list[float]:
    samples = array("h")
    samples.frombytes(raw_path.read_bytes())
    if samples.itemsize != 2 or not samples:
        return []
    window = max(1, int(sample_rate * window_seconds))
    series: list[float] = []
    for start in range(0, len(samples), window):
        chunk = samples[start : start + window]
        if len(chunk) < window * 0.5:
            continue
        rms = math.sqrt(sum(sample * sample for sample in chunk) / len(chunk))
        series.append(rms)
    return series


def _series_similarity(left: list[float], right: list[float]) -> float:
    count = min(len(left), len(right), 480)
    if count < 4:
        return 0.0
    left_values = _resample_series(left, count)
    right_values = _resample_series(right, count)
    left_mean = sum(left_values) / count
    right_mean = sum(right_values) / count
    left_centered = [value - left_mean for value in left_values]
    right_centered = [value - right_mean for value in right_values]
    numerator = sum(a * b for a, b in zip(left_centered, right_centered))
    left_norm = math.sqrt(sum(value * value for value in left_centered))
    right_norm = math.sqrt(sum(value * value for value in right_centered))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    cosine = numerator / (left_norm * right_norm)
    return max(0.0, min(1.0, (cosine + 1.0) / 2.0))


def _resample_series(values: list[float], count: int) -> list[float]:
    if count <= 1:
        return [values[0]]
    if len(values) == count:
        return values[:]
    scale = (len(values) - 1) / (count - 1)
    sampled: list[float] = []
    for index in range(count):
        position = index * scale
        lower = math.floor(position)
        upper = min(len(values) - 1, lower + 1)
        fraction = position - lower
        sampled.append(values[lower] * (1 - fraction) + values[upper] * fraction)
    return sampled


def _aligned_sample_pairs(edl_path: Path | str | None, output_duration: float, max_samples: int) -> list[tuple[float, float]]:
    if not edl_path:
        return []
    path = Path(edl_path)
    if not path.exists():
        return []
    rows: list[tuple[float, float]] = []
    output_cursor = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\|\s*\d+\s*\|\s*(\d+):(\d+)\s*\|\s*(\d+):(\d+)\s*\|", line)
        if not match:
            continue
        source_start = int(match.group(1)) * 60 + int(match.group(2))
        duration = int(match.group(3)) * 60 + int(match.group(4))
        if duration <= 0:
            continue
        for fraction in (0.25, 0.5, 0.75):
            rows.append((min(output_duration - 0.05, output_cursor + duration * fraction), source_start + duration * fraction))
        output_cursor += duration
    if len(rows) <= max_samples:
        return rows
    step = len(rows) / max_samples
    return [rows[math.floor(index * step)] for index in range(max_samples)]


def _uniform_timestamps(duration: float, count: int) -> list[float]:
    if count <= 1:
        return [max(0.0, min(duration - 0.05, duration * 0.5))]
    start = min(duration - 0.05, max(0.0, duration * 0.08))
    end = min(duration - 0.05, max(start, duration * 0.92))
    step = (end - start) / (count - 1)
    return [round(start + step * index, 3) for index in range(count)]


def _extract_frame(video: Path, timestamp: float, output: Path) -> bool:
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-vf",
                "scale=320:-1:flags=lanczos",
                str(output),
            ],
            check=True,
        )
        return output.exists()
    except (OSError, subprocess.CalledProcessError):
        return False


def _dhash(image_path: Path) -> int:
    image = Image.open(image_path).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(image.getdata())
    value = 0
    for y in range(8):
        row = pixels[y * 9 : (y + 1) * 9]
        for x in range(8):
            value = (value << 1) | (1 if row[x] > row[x + 1] else 0)
    return value


def _hash_similarity(left: int, right: int) -> float:
    distance = bin(left ^ right).count("1")
    return round(1.0 - distance / 64, 4)


def _read_sidecar_text(video: Path) -> str:
    parts: list[str] = []
    for suffix in (".txt", ".srt", ".vtt"):
        path = video.with_suffix(suffix)
        if path.exists():
            parts.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _round_optional(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 4)
    return value
