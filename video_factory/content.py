from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageChops, ImageStat


@dataclass(frozen=True)
class ContentProviderStatus:
    name: str
    status: str
    message: str


@dataclass(frozen=True)
class OcrCandidateImage:
    role: str
    path: Path


@dataclass(frozen=True)
class ContentCue:
    sample_index: int
    timestamp: float
    text_density: float
    subtitle_likelihood: float
    interface_likelihood: float
    recognized_text: str
    content_tags: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class ContentAnalysis:
    provider: ContentProviderStatus
    cues: tuple[ContentCue, ...]

    @property
    def coverage(self) -> dict[str, int]:
        return {
            "cue_count": len(self.cues),
            "tagged_count": sum(1 for cue in self.cues if cue.content_tags),
            "recognized_text_count": sum(1 for cue in self.cues if cue.recognized_text.strip()),
        }


def analyze_content_samples(
    sample_paths: Sequence[tuple[float, Path | str]],
    title: str,
    enable_ocr: bool = True,
) -> ContentAnalysis:
    provider = ContentProviderStatus(name="vision_lite", status="fallback", message="local visual content cues")
    recognized_by_path: dict[str, str] = {}
    if enable_ocr:
        provider, recognized_by_path = _try_optional_ocr(sample_paths)

    cues: list[ContentCue] = []
    title_tags = _tags_from_text(title)
    for index, (timestamp, path_like) in enumerate(sample_paths):
        path = Path(path_like)
        metrics = _image_content_metrics(path)
        recognized_text = recognized_by_path.get(str(path), "")
        text_tags = _tags_from_text(recognized_text)
        visual_tags = _visual_tags(metrics)
        content_tags = _ordered_unique((*text_tags, *title_tags, *visual_tags))
        evidence = _content_evidence(metrics, recognized_text, title_tags, visual_tags)
        cues.append(
            ContentCue(
                sample_index=index,
                timestamp=round(float(timestamp), 3),
                text_density=round(metrics["text_density"], 4),
                subtitle_likelihood=round(metrics["subtitle_likelihood"], 4),
                interface_likelihood=round(metrics["interface_likelihood"], 4),
                recognized_text=recognized_text,
                content_tags=content_tags,
                evidence=evidence,
            )
        )
    return ContentAnalysis(provider=provider, cues=tuple(cues))


def analysis_to_dict(analysis: ContentAnalysis) -> dict:
    return {
        "provider": asdict(analysis.provider),
        "coverage": analysis.coverage,
        "cues": [asdict(cue) for cue in analysis.cues],
    }


def write_content_analysis_json(analysis: ContentAnalysis, output_path: Path | str) -> None:
    Path(output_path).write_text(json.dumps(analysis_to_dict(analysis), ensure_ascii=False, indent=2), encoding="utf-8")


def cue_by_sample_index(analysis: ContentAnalysis | None) -> dict[int, ContentCue]:
    if analysis is None:
        return {}
    return {cue.sample_index: cue for cue in analysis.cues}


def build_ocr_candidate_images(
    image_path: Path | str,
    output_dir: Path | str | None = None,
) -> tuple[OcrCandidateImage, ...]:
    path = Path(image_path)
    image = Image.open(path).convert("RGB")
    width, height = image.size
    output = Path(output_dir) if output_dir is not None else path.parent / "ocr_candidates"
    output.mkdir(parents=True, exist_ok=True)

    crop_specs = (
        ("full_frame", (0, 0, width, height)),
        ("subtitle_band", (0, int(height * 0.72), width, height)),
        ("interface_panel", (int(width * 0.06), int(height * 0.10), int(width * 0.94), int(height * 0.88))),
        ("header_band", (0, 0, width, int(height * 0.30))),
    )
    candidates: list[OcrCandidateImage] = []
    for role, box in crop_specs:
        crop = image.crop(_bounded_box(box, width, height))
        candidate_path = output / f"{path.stem}_{role}.jpg"
        _save_ocr_candidate(crop, candidate_path)
        candidates.append(OcrCandidateImage(role=role, path=candidate_path))
    return tuple(candidates)


def _try_optional_ocr(sample_paths: Sequence[tuple[float, Path | str]]) -> tuple[ContentProviderStatus, dict[str, str]]:
    try:
        from ocrmac import ocrmac  # type: ignore
    except Exception as exc:
        return (
            ContentProviderStatus(name="vision_lite", status="fallback", message=f"optional OCR unavailable: {exc}"),
            {},
        )

    recognized_by_path: dict[str, str] = {}
    failures = 0
    attempted_candidates = 0
    for _, path_like in sample_paths:
        path = Path(path_like)
        words_for_sample: list[str] = []
        try:
            candidates = build_ocr_candidate_images(path)
        except BaseException:
            failures += 1
            continue
        for candidate in candidates:
            attempted_candidates += 1
            try:
                result = ocrmac.OCR(
                    str(candidate.path),
                    framework="vision",
                    language_preference=["zh-Hans", "en-US"],
                    confidence_threshold=0.20,
                ).recognize()
            except BaseException:
                failures += 1
                continue
            words_for_sample.extend(str(item[0]).strip() for item in result if item and str(item[0]).strip())
        text = _dedupe_ocr_words(words_for_sample)
        if text:
            recognized_by_path[str(path)] = text

    if recognized_by_path:
        return (
            ContentProviderStatus(
                name="ocrmac",
                status="available",
                message=(
                    f"recognized text in {len(recognized_by_path)}/{len(sample_paths)} sampled frames "
                    f"from {attempted_candidates} OCR candidates"
                ),
            ),
            recognized_by_path,
        )
    if failures:
        return (
            ContentProviderStatus(
                name="ocrmac",
                status="unavailable",
                message=f"OCR failed for {failures}/{len(sample_paths)} sampled frames; using local visual cues",
            ),
            {},
        )
    return (
        ContentProviderStatus(name="ocrmac", status="empty", message="OCR returned no text; using local visual cues"),
        {},
    )


def _bounded_box(box: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    left = max(0, min(left, width - 1))
    top = max(0, min(top, height - 1))
    right = max(left + 1, min(right, width))
    bottom = max(top + 1, min(bottom, height))
    return left, top, right, bottom


def _save_ocr_candidate(image: Image.Image, output_path: Path) -> None:
    width, height = image.size
    target_width = max(1280, width)
    if target_width != width:
        target_height = max(1, round(height * target_width / max(1, width)))
        image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
    image.save(output_path, quality=95)


def _dedupe_ocr_words(words: Sequence[str]) -> str:
    seen: set[str] = set()
    ordered: list[str] = []
    for word in words:
        cleaned = " ".join(str(word).split())
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(cleaned)
        if len(ordered) >= 36:
            break
    return " ".join(ordered)


def _image_content_metrics(path: Path) -> dict[str, float]:
    image = Image.open(path).convert("RGB").resize((320, 180))
    luma = image.convert("L")
    edges = _edge_density(luma)
    bottom = luma.crop((0, 140, 320, 180))
    bottom_edges = _edge_density(bottom)
    bottom_brightness = ImageStat.Stat(bottom).mean[0] / 255.0
    full_brightness = ImageStat.Stat(luma).mean[0] / 255.0
    full_contrast = min(1.0, ImageStat.Stat(luma).stddev[0] / 80.0)
    text_density = min(1.0, edges * 2.2 + full_contrast * 0.12)
    subtitle_likelihood = min(1.0, bottom_edges * 2.4 + (1.0 - abs(bottom_brightness - 0.22)) * 0.18)
    interface_likelihood = min(1.0, edges * 1.8 + max(0.0, full_brightness - 0.45) * 0.6)
    return {
        "text_density": text_density,
        "subtitle_likelihood": subtitle_likelihood,
        "interface_likelihood": interface_likelihood,
        "edge_density": edges,
        "bottom_edge_density": bottom_edges,
        "brightness": full_brightness,
    }


def _edge_density(luma: Image.Image) -> float:
    shifted_x = ImageChops.offset(luma, 1, 0)
    shifted_y = ImageChops.offset(luma, 0, 1)
    diff = ImageChops.lighter(ImageChops.difference(luma, shifted_x), ImageChops.difference(luma, shifted_y))
    histogram = diff.histogram()
    strong = sum(count for value, count in enumerate(histogram) if value >= 24)
    total = max(1, luma.width * luma.height)
    return min(1.0, strong / total)


def _tags_from_text(text: str) -> tuple[str, ...]:
    lower = text.lower()
    patterns = (
        ("codex", ("codex",)),
        ("deepseek", ("deepseek",)),
        ("openai", ("openai",)),
        ("api_key", ("api key", "api_key", "apikey", "密钥", "key")),
        ("configuration", ("配置", "设置", "config", "setting")),
        ("install", ("安装", "下载", "download", "install")),
        ("model", ("模型", "model")),
        ("provider", ("供应商", "provider", "openrouter", "anthropic", "minimax")),
        ("validation", ("验证", "成功", "完成", "result", "success")),
    )
    tags: list[str] = []
    for tag, needles in patterns:
        if any(needle in lower for needle in needles):
            tags.append(tag)
    return tuple(tags)


def _visual_tags(metrics: dict[str, float]) -> tuple[str, ...]:
    tags: list[str] = []
    if metrics["subtitle_likelihood"] >= 0.22:
        tags.append("subtitle")
    if metrics["interface_likelihood"] >= 0.32:
        tags.append("interface")
    if metrics["text_density"] >= 0.18:
        tags.append("text_dense")
    return tuple(tags)


def _content_evidence(
    metrics: dict[str, float],
    recognized_text: str,
    title_tags: Sequence[str],
    visual_tags: Sequence[str],
) -> tuple[str, ...]:
    evidence = [
        f"text_density:{metrics['text_density']:.2f}",
        f"subtitle_likelihood:{metrics['subtitle_likelihood']:.2f}",
        f"interface_likelihood:{metrics['interface_likelihood']:.2f}",
    ]
    if recognized_text.strip():
        evidence.append(f"ocr:{recognized_text[:80]}")
    if title_tags:
        evidence.append("title_tags:" + ",".join(title_tags[:6]))
    if visual_tags:
        evidence.append("visual_tags:" + ",".join(visual_tags[:6]))
    return tuple(evidence)


def _ordered_unique(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value and value not in seen:
            ordered.append(value)
            seen.add(value)
    return tuple(ordered)
