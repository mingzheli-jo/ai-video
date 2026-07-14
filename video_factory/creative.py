from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageChops, ImageStat

from video_factory.audio import AudioAnalysis, AudioCue, AudioProviderStatus, audio_cue_by_sample_index
from video_factory.content import ContentAnalysis, ContentCue, ContentProviderStatus, cue_by_sample_index
from video_factory.semantic import (
    SemanticChapter,
    SemanticProviderStatus,
    SemanticTimeline,
    chapter_by_sample_index,
)


@dataclass(frozen=True)
class CreativeFrameSample:
    index: int
    timestamp: float
    brightness: float
    contrast: float
    sharpness: float
    colorfulness: float
    motion: float


@dataclass(frozen=True)
class CreativeProfile:
    name: str
    confidence: float
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class CreativeMoment:
    sample_index: int
    timestamp: float
    score: float
    role: str
    reason: str


@dataclass(frozen=True)
class CreativeSegment:
    key: str
    start: float
    duration: float
    zoom: float
    crop_x: int
    crop_y: int
    purpose: str
    role: str
    source_sample_index: int
    source_sample_timestamp: float
    semantic_role: str
    role_evidence: str
    content_tags: tuple[str, ...]
    content_evidence: str
    audio_tags: tuple[str, ...]
    audio_evidence: str
    creative_move: str
    semantic_topic: str
    chapter_title: str
    transcript_evidence: str
    source_type: str
    synthetic: bool
    visual_risk_tags: tuple[str, ...]


@dataclass(frozen=True)
class CreativeVariant:
    name: str
    rationale: str
    segments: tuple[CreativeSegment, ...]

    @property
    def total_duration(self) -> float:
        return round(sum(segment.duration for segment in self.segments), 3)


@dataclass(frozen=True)
class CreativeStrategy:
    version: str
    target_duration: float
    coverage_ratio: float
    target_segment_count: int
    treatment: str
    creative_moves: tuple[str, ...]
    production_notes: str


@dataclass(frozen=True)
class CreativePlan:
    title: str
    source_duration: float
    profile: CreativeProfile
    creative_strategy: CreativeStrategy
    content_provider: ContentProviderStatus
    content_coverage: dict[str, int]
    content_cues: tuple[ContentCue, ...]
    audio_provider: AudioProviderStatus
    audio_coverage: dict[str, int]
    audio_cues: tuple[AudioCue, ...]
    semantic_provider: SemanticProviderStatus
    semantic_coverage: dict[str, int]
    semantic_chapters: tuple[SemanticChapter, ...]
    samples: tuple[CreativeFrameSample, ...]
    moments: tuple[CreativeMoment, ...]
    variants: tuple[CreativeVariant, ...]
    recommended_variant_name: str
    cover_candidates: tuple[CreativeMoment, ...]

    @property
    def recommended_variant(self) -> CreativeVariant:
        for variant in self.variants:
            if variant.name == self.recommended_variant_name:
                return variant
        return self.variants[0]


def build_sample_schedule(source_duration: float, sample_count: int = 18) -> list[float]:
    if source_duration <= 0:
        raise ValueError("source_duration must be positive")
    if sample_count <= 0:
        raise ValueError("sample_count must be positive")
    if sample_count == 1:
        return [round(min(source_duration - 0.05, max(0.0, source_duration * 0.35)), 3)]

    first = min(0.7, max(0.0, source_duration * 0.12))
    tail = min(8.0, max(0.4, source_duration * 0.14335))
    last = max(first, source_duration - tail)
    if last <= first:
        last = max(first, source_duration - 0.05)
    step = (last - first) / (sample_count - 1)
    return [round(min(source_duration - 0.05, first + step * index), 3) for index in range(sample_count)]


def classify_creative_profile(samples: Sequence[CreativeFrameSample], title: str) -> CreativeProfile:
    title_lower = title.lower()
    food_hints = ("羊排", "下酒菜", "吃", "烤", "饭", "菜", "美食")
    tutorial_hints = ("codex", "deepseek", "workflow", "教程", "配置", "api", "安装")
    evidence: list[str] = []

    food_hits = [hint for hint in food_hints if hint in title_lower]
    tutorial_hits = [hint for hint in tutorial_hints if hint in title_lower]
    if food_hits:
        evidence.extend(f"title_hint:{hint}" for hint in food_hits[:3])
        return CreativeProfile(name="food_social", confidence=0.82, evidence=tuple(evidence))
    if tutorial_hits:
        evidence.extend(f"title_hint:{hint}" for hint in tutorial_hits[:3])
        return CreativeProfile(name="tutorial_screen", confidence=0.82, evidence=tuple(evidence))

    avg_color = _average_feature(samples, "colorfulness")
    avg_motion = _average_feature(samples, "motion")
    avg_sharpness = _average_feature(samples, "sharpness")
    avg_brightness = _average_feature(samples, "brightness")
    if avg_color >= 0.55 and avg_sharpness >= 0.48:
        evidence.append(f"visual_food_signal:color={avg_color:.2f},sharpness={avg_sharpness:.2f}")
        return CreativeProfile(name="food_social", confidence=0.64, evidence=tuple(evidence))
    if avg_color <= 0.18 and avg_brightness >= 0.45:
        evidence.append(
            f"visual_screen_signal:color={avg_color:.2f},brightness={avg_brightness:.2f},motion={avg_motion:.2f}"
        )
        return CreativeProfile(name="tutorial_screen", confidence=0.66, evidence=tuple(evidence))
    if avg_color <= 0.24 and avg_motion <= 0.22:
        evidence.append(f"visual_tutorial_signal:color={avg_color:.2f},motion={avg_motion:.2f}")
        return CreativeProfile(name="tutorial_screen", confidence=0.62, evidence=tuple(evidence))

    evidence.append(f"fallback:color={avg_color:.2f},motion={avg_motion:.2f}")
    return CreativeProfile(name="generic_live", confidence=0.5, evidence=tuple(evidence))


def analyze_frame_image(
    image_path: Path | str,
    timestamp: float,
    index: int,
    previous_image_path: Path | str | None = None,
) -> CreativeFrameSample:
    image = Image.open(image_path).convert("RGB").resize((96, 96))
    luma = image.convert("L")
    stat = ImageStat.Stat(luma)
    brightness = _clamp01(stat.mean[0] / 255.0)
    contrast = _clamp01(stat.stddev[0] / 80.0)

    pixels = list(image.getdata())
    if not pixels:
        colorfulness = 0.0
    else:
        colorfulness = _clamp01(
            sum(abs(r - g) + abs(((r + g) * 0.5) - b) for r, g, b in pixels) / (len(pixels) * 255.0)
        )

    sharpness = _estimate_sharpness(luma)
    motion = 0.0
    if previous_image_path is not None:
        previous = Image.open(previous_image_path).convert("RGB").resize((96, 96))
        diff = ImageChops.difference(image, previous).convert("L")
        motion = _clamp01(ImageStat.Stat(diff).mean[0] / 55.0)

    return CreativeFrameSample(
        index=index,
        timestamp=round(timestamp, 3),
        brightness=round(brightness, 4),
        contrast=round(contrast, 4),
        sharpness=round(sharpness, 4),
        colorfulness=round(colorfulness, 4),
        motion=round(motion, 4),
    )


def score_sample(sample: CreativeFrameSample, source_duration: float) -> CreativeMoment:
    balanced_brightness = 1.0 - min(1.0, abs(sample.brightness - 0.54) / 0.54)
    late_bonus = 0.08 if sample.timestamp >= source_duration * 0.6 else 0.0
    score = (
        balanced_brightness * 0.16
        + sample.contrast * 0.18
        + sample.sharpness * 0.24
        + sample.colorfulness * 0.26
        + sample.motion * 0.16
        + late_bonus
    )
    score = round(_clamp01(score), 4)
    role = _role_for_sample(sample, source_duration, score)
    return CreativeMoment(
        sample_index=sample.index,
        timestamp=sample.timestamp,
        score=score,
        role=role,
        reason=_reason_for_role(role),
    )


def build_creative_plan(
    source_duration: float,
    samples: Sequence[CreativeFrameSample],
    title: str,
    content_analysis: ContentAnalysis | None = None,
    audio_analysis: AudioAnalysis | None = None,
    semantic_timeline: SemanticTimeline | None = None,
    production_options: dict | None = None,
) -> CreativePlan:
    if source_duration <= 0:
        raise ValueError("source_duration must be positive")
    if not samples:
        raise ValueError("samples must not be empty")

    sample_tuple = tuple(samples)
    profile = classify_creative_profile(sample_tuple, title)
    cue_map = cue_by_sample_index(content_analysis)
    audio_cue_map = audio_cue_by_sample_index(audio_analysis)
    content_provider = (
        content_analysis.provider
        if content_analysis is not None
        else ContentProviderStatus(name="none", status="not_requested", message="content analysis not provided")
    )
    content_coverage = content_analysis.coverage if content_analysis is not None else {}
    content_cues = content_analysis.cues if content_analysis is not None else ()
    audio_provider = (
        audio_analysis.provider
        if audio_analysis is not None
        else AudioProviderStatus(name="none", status="not_requested", message="audio analysis not provided")
    )
    audio_coverage = audio_analysis.coverage if audio_analysis is not None else {}
    audio_cues = audio_analysis.cues if audio_analysis is not None else ()
    semantic_provider = (
        semantic_timeline.provider
        if semantic_timeline is not None
        else SemanticProviderStatus(name="none", status="not_requested", message="semantic timeline not provided")
    )
    semantic_coverage = semantic_timeline.coverage if semantic_timeline is not None else {}
    semantic_chapters = semantic_timeline.chapters if semantic_timeline is not None else ()
    profile = _refine_profile_with_content_signals(profile, sample_tuple, content_cues, semantic_chapters)
    chapter_map = chapter_by_sample_index(semantic_timeline)
    moments = tuple(score_sample(sample, source_duration) for sample in sample_tuple)
    by_index = {sample.index: sample for sample in sample_tuple}
    risk_tags_by_index = {
        sample.index: _source_frame_risk_tags(sample, cue_map.get(sample.index)) for sample in sample_tuple
    }
    raw_cover_candidates = tuple(
        sorted(
            (moment for moment in moments if moment.role in {"cover_candidate", "finish_or_reveal"}),
            key=lambda moment: moment.score,
            reverse=True,
        )[:4]
    )
    safe_cover_candidates = tuple(
        moment
        for moment in raw_cover_candidates
        if "template_like_source_frame" not in risk_tags_by_index.get(moment.sample_index, ())
    )
    safe_hook_pool = [
        moment for moment in moments if "template_like_source_frame" not in risk_tags_by_index.get(moment.sample_index, ())
    ]
    cover_candidates = safe_cover_candidates or raw_cover_candidates
    hook = (
        safe_cover_candidates[0]
        if safe_cover_candidates
        else max(safe_hook_pool or list(moments), key=lambda moment: moment.score)
    )
    target_count = _target_segment_count_for_options(source_duration, production_options)
    target_duration = _target_creative_duration_for_options(source_duration, production_options)
    planned_segment_duration = _segment_duration_for_count(
        source_duration,
        target_count,
        target_duration=target_duration,
        production_options=production_options,
    )
    ordered_moments = _story_ordered_moments(
        profile=profile,
        source_duration=source_duration,
        moments=moments,
        hook=hook,
        target_count=target_count,
    )
    selected = _select_non_overlapping_moments(
        source_duration,
        ordered_moments,
        target_count=target_count,
        content_cues=cue_map,
        risk_tags_by_index=risk_tags_by_index,
        segment_duration=planned_segment_duration,
    )
    selected = _release_order_selected_moments(profile, source_duration, selected)
    segment_duration = _segment_duration_for_count(
        source_duration,
        len(selected),
        target_duration=target_duration,
        production_options=production_options,
    )
    variant_segments = tuple(
        _segment_from_moment(
            source_duration=source_duration,
            moment=moment,
            sample=by_index[moment.sample_index],
            order=index,
            total_count=len(selected),
            first=index == 0,
            profile=profile,
            content_cue=cue_map.get(moment.sample_index),
            audio_cue=audio_cue_map.get(moment.sample_index),
            semantic_chapter=chapter_map.get(moment.sample_index),
            segment_duration=segment_duration,
        )
        for index, moment in enumerate(selected)
    )
    if source_duration >= 300 and _requires_source_guided_longform_body(production_options):
        variant_segments = _fit_source_guided_segments_without_overlap(variant_segments, source_duration)
    variant = CreativeVariant(
        name="cover_first_story",
        rationale="先用最强真实画面建立结果感，再回到正文时间线推进，最后用清晰验证画面收束。",
        segments=variant_segments,
    )
    chronological = CreativeVariant(
        name="chronological_density",
        rationale="保留源视频时间顺序，只压缩低信息密度段落。",
        segments=tuple(sorted(variant.segments, key=lambda segment: segment.start)),
    )
    plan = CreativePlan(
        title=title,
        source_duration=round(source_duration, 3),
        profile=profile,
        creative_strategy=_creative_strategy(
            source_duration,
            profile,
            target_duration,
            target_count,
            production_options=production_options,
        ),
        content_provider=content_provider,
        content_coverage=content_coverage,
        content_cues=content_cues,
        audio_provider=audio_provider,
        audio_coverage=audio_coverage,
        audio_cues=audio_cues,
        semantic_provider=semantic_provider,
        semantic_coverage=semantic_coverage,
        semantic_chapters=semantic_chapters,
        samples=sample_tuple,
        moments=moments,
        variants=(variant, chronological),
        recommended_variant_name=variant.name,
        cover_candidates=cover_candidates,
    )
    return plan


def ranges_overlap(a_start: float, a_end: float, b_start: float, b_end: float, tolerance: float = 0.001) -> bool:
    return max(a_start, b_start) < min(a_end, b_end) - tolerance


def _refine_profile_with_content_signals(
    profile: CreativeProfile,
    samples: Sequence[CreativeFrameSample],
    content_cues: Sequence[ContentCue],
    semantic_chapters: Sequence[SemanticChapter],
) -> CreativeProfile:
    if profile.name != "generic_live":
        return profile
    if not samples:
        return profile

    cue_count = len(content_cues)
    tag_counts: dict[str, int] = {}
    for cue in content_cues:
        for tag in cue.content_tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    text_dense_ratio = tag_counts.get("text_dense", 0) / max(1, cue_count)
    subtitle_ratio = tag_counts.get("subtitle", 0) / max(1, cue_count)
    interface_ratio = tag_counts.get("interface", 0) / max(1, cue_count)
    tutorial_tag_ratio = sum(
        tag_counts.get(tag, 0)
        for tag in ("codex", "deepseek", "api_key", "configuration", "model", "provider", "validation")
    ) / max(1, cue_count)
    chapter_text = " ".join(
        " ".join((chapter.title, chapter.topic, *chapter.evidence)).lower() for chapter in semantic_chapters
    )
    chapter_has_tutorial_signal = any(
        needle in chapter_text
        for needle in ("codex", "deepseek", "api", "配置", "设置", "验证", "安装", "模型", "key")
    )

    avg_color = _average_feature(samples, "colorfulness")
    avg_brightness = _average_feature(samples, "brightness")
    avg_motion = _average_feature(samples, "motion")
    if cue_count >= 4 and text_dense_ratio >= 0.55 and subtitle_ratio >= 0.50 and avg_color <= 0.18:
        evidence = (
            "content_screen_signal:"
            f"text_dense={text_dense_ratio:.2f},subtitle={subtitle_ratio:.2f},"
            f"color={avg_color:.2f},brightness={avg_brightness:.2f},motion={avg_motion:.2f}"
        )
        return CreativeProfile(
            name="tutorial_screen",
            confidence=max(0.68, profile.confidence),
            evidence=(*profile.evidence, evidence),
        )
    if (interface_ratio >= 0.25 or tutorial_tag_ratio >= 0.18 or chapter_has_tutorial_signal) and (
        text_dense_ratio >= 0.35 or subtitle_ratio >= 0.35
    ):
        evidence = (
            "content_tutorial_signal:"
            f"interface={interface_ratio:.2f},tutorial_tags={tutorial_tag_ratio:.2f},"
            f"chapters={chapter_has_tutorial_signal}"
        )
        return CreativeProfile(
            name="tutorial_screen",
            confidence=max(0.66, profile.confidence),
            evidence=(*profile.evidence, evidence),
        )
    return profile


def plan_to_dict(plan: CreativePlan) -> dict:
    data = asdict(plan)
    data["profile_confidence"] = plan.profile.confidence
    data["profile_evidence"] = list(plan.profile.evidence)
    data["recommended_variant"] = asdict(plan.recommended_variant)
    for variant in data["variants"]:
        variant["total_duration"] = round(sum(segment["duration"] for segment in variant["segments"]), 3)
    data["recommended_variant"]["total_duration"] = plan.recommended_variant.total_duration
    return data


def write_creative_plan_json(plan: CreativePlan, output_path: Path | str) -> None:
    Path(output_path).write_text(json.dumps(plan_to_dict(plan), ensure_ascii=False, indent=2), encoding="utf-8")


def write_candidate_edl(plan: CreativePlan, output_path: Path | str) -> None:
    lines = [
        "# Creative Candidate EDL",
        "",
        f"Title: `{plan.title}`",
        f"Recommended: `{plan.recommended_variant.name}`",
        f"Target duration: `{_format_seconds(plan.recommended_variant.total_duration)}`",
        f"Profile: `{plan.profile.name}` (`{plan.profile.confidence:.2f}`)",
        "",
        "| # | Chapter | Semantic Role | Creative Move | Visual Role | Content | Audio | Source In | Duration | Lens | Reason |",
        "|---|---|---|---|---|---|---|---:|---:|---:|---|",
    ]
    for index, segment in enumerate(plan.recommended_variant.segments, start=1):
        content = ", ".join(segment.content_tags[:4]) or segment.content_evidence or "none"
        audio = ", ".join(segment.audio_tags[:4]) or segment.audio_evidence or "none"
        chapter = segment.chapter_title or segment.semantic_topic or "none"
        lines.append(
            f"| {index} | {chapter} | {segment.semantic_role} | {segment.creative_move} | {segment.role} | {content} | {audio} | {_format_seconds(segment.start)} | "
            f"{_format_seconds(segment.duration)} | {segment.zoom:.2f}x | {segment.purpose} |"
        )
    Path(output_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _select_non_overlapping_moments(
    source_duration: float,
    moments: Sequence[CreativeMoment],
    target_count: int,
    content_cues: dict[int, ContentCue] | None = None,
    risk_tags_by_index: dict[int, tuple[str, ...]] | None = None,
    segment_duration: float | None = None,
) -> list[CreativeMoment]:
    selected: list[CreativeMoment] = []
    ranges: list[tuple[float, float]] = []
    deferred_repeated_content: list[CreativeMoment] = []
    deferred_template_like: list[CreativeMoment] = []
    content_signature_counts: dict[str, int] = {}
    max_signature_uses = _max_content_signature_uses(source_duration, target_count)
    template_like_budget = _template_like_source_frame_budget(target_count)
    template_like_count = 0
    late_template_like_count = 0

    def can_add_template_like(moment: CreativeMoment) -> bool:
        nonlocal template_like_count, late_template_like_count
        if not _is_template_like_moment(moment, risk_tags_by_index):
            return True
        if template_like_count >= template_like_budget:
            return False
        if _is_late_template_like_moment(source_duration, moment, risk_tags_by_index) and late_template_like_count >= 1:
            return False
        return True

    def add_moment(moment: CreativeMoment, start: float, end: float) -> None:
        nonlocal template_like_count, late_template_like_count
        selected.append(moment)
        ranges.append((start, end))
        if _is_template_like_moment(moment, risk_tags_by_index):
            template_like_count += 1
            if _is_late_template_like_moment(source_duration, moment, risk_tags_by_index):
                late_template_like_count += 1

    for moment in moments:
        duration = segment_duration or _segment_duration(source_duration)
        start = _segment_start(moment.timestamp, duration, source_duration)
        end = start + duration
        if any(ranges_overlap(start, end, existing_start, existing_end) for existing_start, existing_end in ranges):
            continue
        if not can_add_template_like(moment):
            deferred_template_like.append(moment)
            continue
        signature = _content_signature(content_cues.get(moment.sample_index) if content_cues else None)
        signature_limit = _max_content_signature_uses(source_duration, target_count, signature)
        if signature and content_signature_counts.get(signature, 0) >= signature_limit:
            deferred_repeated_content.append(moment)
            continue
        add_moment(moment, start, end)
        if signature:
            content_signature_counts[signature] = content_signature_counts.get(signature, 0) + 1
        if len(selected) >= target_count:
            break

    if len(selected) < target_count:
        for moment in deferred_repeated_content:
            duration = segment_duration or _segment_duration(source_duration)
            start = _segment_start(moment.timestamp, duration, source_duration)
            end = start + duration
            if any(ranges_overlap(start, end, existing_start, existing_end) for existing_start, existing_end in ranges):
                continue
            if not can_add_template_like(moment):
                deferred_template_like.append(moment)
                continue
            signature = _content_signature(content_cues.get(moment.sample_index) if content_cues else None)
            signature_limit = _max_content_signature_uses(source_duration, target_count, signature)
            if source_duration >= 300 and signature and content_signature_counts.get(signature, 0) >= signature_limit:
                continue
            add_moment(moment, start, end)
            if signature:
                content_signature_counts[signature] = content_signature_counts.get(signature, 0) + 1
            if len(selected) >= target_count:
                break

    if len(selected) < target_count:
        for moment in deferred_template_like:
            duration = segment_duration or _segment_duration(source_duration)
            start = _segment_start(moment.timestamp, duration, source_duration)
            end = start + duration
            if any(ranges_overlap(start, end, existing_start, existing_end) for existing_start, existing_end in ranges):
                continue
            if not can_add_template_like(moment):
                continue
            add_moment(moment, start, end)
            if len(selected) >= target_count:
                break

    if not selected:
        first = moments[0]
        selected.append(first)
    selected = _top_up_safe_moments(
        source_duration,
        selected,
        moments,
        target_count,
        risk_tags_by_index,
        segment_duration=segment_duration,
        content_cues=content_cues,
        max_signature_uses=max_signature_uses,
    )
    selected = _rebalance_template_like_selection_budget(
        source_duration,
        selected,
        moments,
        risk_tags_by_index,
        segment_duration=segment_duration,
    )
    selected = _top_up_safe_moments(
        source_duration,
        selected,
        moments,
        target_count,
        risk_tags_by_index,
        segment_duration=segment_duration,
        content_cues=content_cues,
        max_signature_uses=max_signature_uses,
    )
    return _rebalance_template_like_selection_budget(
        source_duration,
        selected,
        moments,
        risk_tags_by_index,
        segment_duration=segment_duration,
    )


def _template_like_source_frame_budget(segment_count: int) -> int:
    return max(1, math.floor(segment_count * 0.18))


def _is_template_like_moment(
    moment: CreativeMoment,
    risk_tags_by_index: dict[int, tuple[str, ...]] | None,
) -> bool:
    if risk_tags_by_index is None:
        return False
    return "template_like_source_frame" in risk_tags_by_index.get(moment.sample_index, ())


def _is_late_template_like_moment(
    source_duration: float,
    moment: CreativeMoment,
    risk_tags_by_index: dict[int, tuple[str, ...]] | None,
) -> bool:
    return _is_template_like_moment(moment, risk_tags_by_index) and moment.timestamp >= source_duration * 0.72


def _rebalance_template_like_selection_budget(
    source_duration: float,
    selected: Sequence[CreativeMoment],
    candidates: Sequence[CreativeMoment],
    risk_tags_by_index: dict[int, tuple[str, ...]] | None,
    segment_duration: float | None = None,
) -> list[CreativeMoment]:
    if risk_tags_by_index is None:
        return list(selected)
    balanced = list(selected)

    def template_like_positions() -> list[int]:
        return [
            index
            for index, moment in enumerate(balanced)
            if _is_template_like_moment(moment, risk_tags_by_index)
        ]

    while len(template_like_positions()) > _template_like_source_frame_budget(len(balanced)):
        positions = template_like_positions()
        remove_index = max(
            positions,
            key=lambda index: (
                _is_late_template_like_moment(source_duration, balanced[index], risk_tags_by_index),
                balanced[index].timestamp,
                -balanced[index].score,
            ),
        )
        removed = balanced[remove_index]
        remaining = [moment for index, moment in enumerate(balanced) if index != remove_index]
        replacement = _safe_replacement_for_template_like_moment(
            source_duration=source_duration,
            removed=removed,
            selected=remaining,
            candidates=candidates,
            risk_tags_by_index=risk_tags_by_index,
            segment_duration=segment_duration,
        )
        if replacement is None:
            balanced.pop(remove_index)
        else:
            balanced[remove_index] = replacement
    return balanced


def _top_up_safe_moments(
    source_duration: float,
    selected: Sequence[CreativeMoment],
    candidates: Sequence[CreativeMoment],
    target_count: int,
    risk_tags_by_index: dict[int, tuple[str, ...]] | None,
    segment_duration: float | None = None,
    content_cues: dict[int, ContentCue] | None = None,
    max_signature_uses: int | None = None,
) -> list[CreativeMoment]:
    topped_up = list(selected)
    if len(topped_up) >= target_count:
        return topped_up
    duration = segment_duration or _segment_duration(source_duration)
    selected_indices = {moment.sample_index for moment in topped_up}
    signature_counts: dict[str, int] = {}
    for moment in topped_up:
        signature = _content_signature(content_cues.get(moment.sample_index) if content_cues else None)
        if signature:
            signature_counts[signature] = signature_counts.get(signature, 0) + 1
    selected_ranges = [
        (_segment_start(moment.timestamp, duration, source_duration), _segment_start(moment.timestamp, duration, source_duration) + duration)
        for moment in topped_up
    ]
    for candidate in candidates:
        if len(topped_up) >= target_count:
            break
        if candidate.sample_index in selected_indices:
            continue
        if _is_template_like_moment(candidate, risk_tags_by_index):
            continue
        signature = _content_signature(content_cues.get(candidate.sample_index) if content_cues else None)
        signature_limit = _max_content_signature_uses(source_duration, target_count, signature)
        if source_duration >= 300 and signature and signature_counts.get(signature, 0) >= signature_limit:
            continue
        start = _segment_start(candidate.timestamp, duration, source_duration)
        end = start + duration
        if any(ranges_overlap(start, end, existing_start, existing_end) for existing_start, existing_end in selected_ranges):
            continue
        topped_up.append(candidate)
        selected_indices.add(candidate.sample_index)
        if signature:
            signature_counts[signature] = signature_counts.get(signature, 0) + 1
        selected_ranges.append((start, end))
    return topped_up


def _safe_replacement_for_template_like_moment(
    source_duration: float,
    removed: CreativeMoment,
    selected: Sequence[CreativeMoment],
    candidates: Sequence[CreativeMoment],
    risk_tags_by_index: dict[int, tuple[str, ...]],
    segment_duration: float | None = None,
) -> CreativeMoment | None:
    selected_indices = {moment.sample_index for moment in selected}
    duration = segment_duration or _segment_duration(source_duration)
    selected_ranges = [
        (_segment_start(moment.timestamp, duration, source_duration), _segment_start(moment.timestamp, duration, source_duration) + duration)
        for moment in selected
    ]
    for candidate in sorted(
        candidates,
        key=lambda moment: (
            abs(moment.timestamp - removed.timestamp),
            -moment.score,
        ),
    ):
        if candidate.sample_index in selected_indices:
            continue
        if _is_template_like_moment(candidate, risk_tags_by_index):
            continue
        start = _segment_start(candidate.timestamp, duration, source_duration)
        end = start + duration
        if any(ranges_overlap(start, end, existing_start, existing_end) for existing_start, existing_end in selected_ranges):
            continue
        return candidate
    return None


def _release_order_selected_moments(
    profile: CreativeProfile,
    source_duration: float,
    selected: Sequence[CreativeMoment],
) -> list[CreativeMoment]:
    if profile.name == "tutorial_screen" and source_duration >= 300 and len(selected) > 1:
        hook = selected[0]
        return [hook] + sorted(selected[1:], key=lambda moment: (moment.timestamp, -moment.score))
    return list(selected)


def _story_ordered_moments(
    profile: CreativeProfile,
    source_duration: float,
    moments: Sequence[CreativeMoment],
    hook: CreativeMoment,
    target_count: int,
) -> list[CreativeMoment]:
    if profile.name == "tutorial_screen":
        return _tutorial_story_order(source_duration, moments, hook, target_count)
    return [hook] + sorted(
        (moment for moment in moments if moment.sample_index != hook.sample_index),
        key=lambda moment: (moment.timestamp, -moment.score),
    )


def _tutorial_story_order(
    source_duration: float,
    moments: Sequence[CreativeMoment],
    hook: CreativeMoment,
    target_count: int,
) -> list[CreativeMoment]:
    if source_duration >= 300 and target_count >= 10:
        return _longform_tutorial_story_order(source_duration, moments, hook, target_count)

    remaining = [moment for moment in moments if moment.sample_index != hook.sample_index]
    seen: set[int] = set()
    ordered: list[CreativeMoment] = []

    def add(candidates: Sequence[CreativeMoment], limit: int | None = None) -> None:
        added = 0
        for candidate in candidates:
            if candidate.sample_index in seen:
                continue
            ordered.append(candidate)
            seen.add(candidate.sample_index)
            added += 1
            if limit is not None and added >= limit:
                break

    setup = sorted(
        (moment for moment in remaining if moment.timestamp <= source_duration * 0.2 and moment.role == "context"),
        key=lambda moment: (moment.timestamp, -moment.score),
    )
    operations = sorted(
        (moment for moment in remaining if moment.role == "process_action"),
        key=lambda moment: (-moment.score, moment.timestamp),
    )
    operations = _spaced_moments(operations, min_gap=_minimum_story_spacing(source_duration))
    configuration = sorted(
        (
            moment
            for moment in remaining
            if source_duration * 0.22 <= moment.timestamp <= source_duration * 0.72
            and moment.role in {"context", "detail_closeup"}
        ),
        key=lambda moment: (-moment.score, moment.timestamp),
    )
    validation = sorted(
        (moment for moment in remaining if moment.role == "finish_or_reveal" or moment.timestamp >= source_duration * 0.72),
        key=lambda moment: (moment.score, moment.timestamp),
        reverse=True,
    )

    ordered.append(hook)
    seen.add(hook.sample_index)
    add(setup, limit=1)

    reserved = 1
    if setup:
        reserved += 1
    if configuration:
        reserved += 1
    if validation:
        reserved += 1
    operation_slots = max(0, target_count - reserved)
    lead_operation_count = min(1, operation_slots)

    add(operations[:lead_operation_count])
    add(configuration, limit=1)
    add(operations[lead_operation_count:operation_slots])
    add(validation, limit=1)
    add(sorted(remaining, key=lambda moment: (-moment.score, moment.timestamp)))
    return ordered


def _longform_tutorial_story_order(
    source_duration: float,
    moments: Sequence[CreativeMoment],
    hook: CreativeMoment,
    target_count: int,
) -> list[CreativeMoment]:
    role_arc = _longform_tutorial_role_arc(target_count)
    remaining = [moment for moment in moments if moment.sample_index != hook.sample_index]
    seen: set[int] = {hook.sample_index}
    ordered: list[CreativeMoment] = [hook]
    if remaining and target_count > 1:
        opening_anchor = min(remaining, key=lambda moment: (moment.timestamp, -moment.score))
        ordered.append(opening_anchor)
        seen.add(opening_anchor.sample_index)
    desired_roles = role_arc[len(ordered) :]
    minimum_forward_step = max(1.0, _segment_duration(source_duration) * 0.65)
    minimum_timestamp = ordered[-1].timestamp + minimum_forward_step if len(ordered) > 1 else 0.0

    for slot_index, desired_role in enumerate(desired_roles, start=len(ordered)):
        candidates = [moment for moment in remaining if moment.sample_index not in seen]
        if not candidates:
            break
        forward_candidates = [moment for moment in candidates if moment.timestamp + 0.001 >= minimum_timestamp]
        if not forward_candidates:
            forward_candidates = candidates
        target_timestamp = _longform_role_target_timestamp(source_duration, desired_roles, desired_role, slot_index)
        candidate = max(
            forward_candidates,
            key=lambda moment: _longform_tutorial_candidate_score(
                moment=moment,
                desired_role=desired_role,
                target_timestamp=target_timestamp,
                source_duration=source_duration,
            ),
        )
        ordered.append(candidate)
        seen.add(candidate.sample_index)
        minimum_timestamp = candidate.timestamp + minimum_forward_step

    ordered.extend(
        sorted(
            (moment for moment in remaining if moment.sample_index not in seen),
            key=lambda moment: (moment.timestamp, -moment.score),
        )
    )
    return ordered


def _max_content_signature_uses(source_duration: float, target_count: int, signature: str = "") -> int:
    if signature.startswith("text:"):
        if source_duration >= 300:
            return max(2, math.ceil(target_count * 0.22))
        if source_duration >= 120:
            return max(2, math.ceil(target_count * 0.34))
        return max(1, math.ceil(target_count * 0.4))
    if source_duration >= 300:
        return max(2, math.ceil(target_count * 0.72))
    if source_duration >= 120:
        return max(2, math.ceil(target_count * 0.34))
    return max(1, math.ceil(target_count * 0.4))


def _content_signature(content_cue: ContentCue | None) -> str:
    if content_cue is None:
        return ""
    text = content_cue.recognized_text.strip()
    if text:
        normalized = "".join(char.lower() for char in text if char.isalnum())
        if normalized:
            return f"text:{normalized[:36]}"
    if content_cue.content_tags:
        return f"tags:{'|'.join(content_cue.content_tags[:3])}"
    return ""


def _longform_tutorial_role_arc(total_count: int) -> tuple[str, ...]:
    if total_count <= 1:
        return ("tutorial_hook",)
    finale_count = 2 if total_count >= 12 else 1
    body_count = max(0, total_count - 1 - finale_count)
    body_pattern = (
        "interface_state",
        "configuration_detail",
        "operation_step",
        "interface_state",
        "configuration_detail",
    )
    roles = ["tutorial_hook"]
    roles.extend(body_pattern[index % len(body_pattern)] for index in range(body_count))
    roles.extend("result_validation" for _ in range(finale_count))
    return tuple(roles[:total_count])


def _longform_generic_role_arc(total_count: int) -> tuple[str, ...]:
    if total_count <= 1:
        return ("visual_hook",)
    finale_count = 2 if total_count >= 12 else 1
    body_count = max(0, total_count - 1 - finale_count)
    body_pattern = (
        "context_bridge",
        "action_moment",
        "decision_moment",
        "detail_moment",
        "context_bridge",
    )
    roles = ["visual_hook"]
    roles.extend(body_pattern[index % len(body_pattern)] for index in range(body_count))
    roles.extend("result_moment" for _ in range(finale_count))
    return tuple(roles[:total_count])


def _longform_role_target_timestamp(
    source_duration: float,
    desired_roles: Sequence[str],
    desired_role: str,
    slot_index: int,
) -> float:
    slot_count = max(1, len(desired_roles))
    if desired_role == "result_validation":
        result_slots = [index for index, role in enumerate(desired_roles, start=1) if role == "result_validation"]
        result_position = result_slots.index(slot_index) if slot_index in result_slots else 0
        progress = 0.82 + 0.13 * (result_position / max(1, len(result_slots) - 1))
    else:
        progress = 0.04 + 0.74 * ((slot_index - 1) / max(1, slot_count - 1))
    return max(0.7, min(source_duration - 0.5, source_duration * progress))


def _longform_tutorial_candidate_score(
    moment: CreativeMoment,
    desired_role: str,
    target_timestamp: float,
    source_duration: float,
) -> float:
    distance_penalty = abs(moment.timestamp - target_timestamp) / max(1.0, source_duration)
    role_bonus = 0.0
    if desired_role == "interface_state" and moment.role == "context":
        role_bonus = 0.26
    elif desired_role == "operation_step" and moment.role == "process_action":
        role_bonus = 0.42
    elif desired_role == "configuration_detail" and moment.role in {"detail_closeup", "context"}:
        role_bonus = 0.28
    elif desired_role == "result_validation" and (
        moment.role in {"finish_or_reveal", "cover_candidate"} or moment.timestamp >= source_duration * 0.72
    ):
        role_bonus = 0.46
    return moment.score * 0.38 + role_bonus - distance_penalty * 0.82


def _spaced_moments(candidates: Sequence[CreativeMoment], min_gap: float) -> list[CreativeMoment]:
    spaced: list[CreativeMoment] = []
    for candidate in candidates:
        if any(abs(candidate.timestamp - selected.timestamp) < min_gap for selected in spaced):
            continue
        spaced.append(candidate)
    return spaced


def _minimum_story_spacing(source_duration: float) -> float:
    if source_duration >= 180:
        return min(60.0, max(45.0, source_duration * 0.08))
    return min(24.0, max(12.0, source_duration * 0.08))


def _segment_from_moment(
    source_duration: float,
    moment: CreativeMoment,
    sample: CreativeFrameSample,
    order: int,
    total_count: int,
    first: bool,
    profile: CreativeProfile,
    content_cue: ContentCue | None,
    audio_cue: AudioCue | None,
    semantic_chapter: SemanticChapter | None,
    segment_duration: float | None = None,
) -> CreativeSegment:
    duration = segment_duration if segment_duration is not None else _segment_duration(source_duration)
    if first:
        duration = _cold_open_segment_duration(source_duration, duration)
    start = _segment_start(moment.timestamp, duration, source_duration)
    if first:
        start = _cold_open_segment_start(moment.timestamp, duration, source_duration)
    semantic_role, role_evidence = _semantic_role_for_moment(
        profile,
        moment,
        sample,
        source_duration,
        first,
        order,
        total_count,
    )
    purpose = _purpose_for_semantic_role(semantic_role, role_evidence, first)
    content_tags = content_cue.content_tags if content_cue is not None else ()
    content_evidence = _segment_content_evidence(content_cue)
    audio_tags = audio_cue.audio_tags if audio_cue is not None else ()
    audio_evidence = _segment_audio_evidence(audio_cue)
    creative_move = _creative_move_for_semantic_role(semantic_role, first)
    semantic_topic = semantic_chapter.topic if semantic_chapter is not None else ""
    chapter_title = semantic_chapter.title if semantic_chapter is not None else ""
    transcript_evidence = _segment_transcript_evidence(semantic_chapter)
    visual_risk_tags = _source_frame_risk_tags(sample, content_cue)
    zoom = 1.1 if moment.role in {"cover_candidate", "finish_or_reveal"} else 1.06
    return CreativeSegment(
        key=f"creative_{order:02d}_{moment.role}",
        start=round(start, 3),
        duration=round(duration, 3),
        zoom=zoom,
        crop_x=0,
        crop_y=0,
        purpose=purpose,
        role=moment.role,
        source_sample_index=sample.index,
        source_sample_timestamp=round(sample.timestamp, 3),
        semantic_role=semantic_role,
        role_evidence=role_evidence,
        content_tags=content_tags,
        content_evidence=content_evidence,
        audio_tags=audio_tags,
        audio_evidence=audio_evidence,
        creative_move=creative_move,
        semantic_topic=semantic_topic,
        chapter_title=chapter_title,
        transcript_evidence=transcript_evidence,
        source_type="source_video",
        synthetic=False,
        visual_risk_tags=visual_risk_tags,
    )


def _source_frame_risk_tags(sample: CreativeFrameSample, content_cue: ContentCue | None) -> tuple[str, ...]:
    if content_cue is None:
        return ()
    tags = set(content_cue.content_tags)
    text_or_caption_like = "subtitle" in tags or content_cue.subtitle_likelihood >= 0.22
    weak_interface = "interface" not in tags and content_cue.interface_likelihood < 0.12
    low_detail_no_text = sample.colorfulness < 0.14 and sample.sharpness < 0.18 and not content_cue.recognized_text.strip()
    dark_low_detail = sample.brightness < 0.32 and sample.colorfulness < 0.14 and sample.sharpness < 0.26
    bright_flat_title_layout = (
        sample.brightness >= 0.90
        and sample.contrast < 0.45
        and low_detail_no_text
        and content_cue.subtitle_likelihood >= 0.35
    )
    sparse_text_card = content_cue.text_density < 0.18 and not content_cue.recognized_text.strip()
    if (text_or_caption_like and weak_interface and dark_low_detail and sparse_text_card) or bright_flat_title_layout:
        return ("template_like_source_frame",)
    return ()


def _segment_content_evidence(content_cue: ContentCue | None) -> str:
    if content_cue is None:
        return ""
    if content_cue.recognized_text.strip():
        return f"OCR: {content_cue.recognized_text[:80]}"
    if content_cue.evidence:
        return "; ".join(content_cue.evidence[:3])
    return ""


def _segment_audio_evidence(audio_cue: AudioCue | None) -> str:
    if audio_cue is None:
        return ""
    if audio_cue.evidence:
        return "; ".join(audio_cue.evidence[:3])
    return f"speech_likelihood:{audio_cue.speech_likelihood:.2f}"


def _segment_transcript_evidence(semantic_chapter: SemanticChapter | None) -> str:
    if semantic_chapter is None:
        return ""
    if semantic_chapter.evidence:
        return " / ".join(semantic_chapter.evidence[:2])
    return semantic_chapter.title


def _target_segment_count(source_duration: float) -> int:
    if source_duration < 12:
        return 2
    if source_duration < 40:
        return 4
    if source_duration < 120:
        return 6
    target_duration = _target_creative_duration(source_duration)
    segment_duration = _segment_duration(source_duration)
    return min(36, max(8, math.ceil(target_duration / segment_duration)))


def _target_segment_count_for_options(source_duration: float, production_options: dict | None) -> int:
    target_count = _target_segment_count(source_duration)
    if not production_options:
        return target_count
    strength = str(production_options.get("creative_strength", "balanced"))
    policy = str(production_options.get("target_duration_policy", "source_guided"))
    if policy == "short_summary":
        target_count = max(4, math.floor(target_count * 0.52))
    elif policy == "retain_core":
        target_count = min(42, max(target_count, math.ceil(target_count * 1.14)))
    if strength == "strong":
        target_count = min(42, math.ceil(target_count * 1.12))
    elif strength == "light":
        target_count = max(4, math.floor(target_count * 0.82))
    return target_count


def _segment_duration(source_duration: float) -> float:
    if source_duration < 12:
        return max(1.4, source_duration * 0.28)
    if source_duration < 40:
        return min(4.0, max(2.8, source_duration * 0.145))
    if source_duration < 120:
        return min(6.0, max(3.5, source_duration * 0.075))
    if source_duration < 300:
        return min(9.0, max(6.0, source_duration * 0.035))
    return min(12.0, max(9.0, source_duration * 0.019))


def _segment_duration_for_count(
    source_duration: float,
    segment_count: int,
    target_duration: float | None = None,
    production_options: dict | None = None,
) -> float:
    duration = _segment_duration(source_duration)
    if source_duration < 300 or segment_count <= 1:
        return duration
    cold_open_duration = _cold_open_segment_duration(source_duration, duration)
    minimum_total = _minimum_creative_longform_plan_duration(source_duration) + 2.0
    if _requires_source_guided_longform_body(production_options) and target_duration:
        minimum_total = max(minimum_total, float(target_duration) * 0.92)
    required_body_duration = (minimum_total - cold_open_duration) / max(1, segment_count - 1)
    if required_body_duration <= duration:
        return duration
    return min(32.0, required_body_duration)


def _fit_source_guided_segments_without_overlap(
    segments: Sequence[CreativeSegment],
    source_duration: float,
) -> tuple[CreativeSegment, ...]:
    ordered = sorted(enumerate(segments), key=lambda item: (item[1].start, item[1].start + item[1].duration, item[0]))
    adjusted: dict[int, CreativeSegment] = {}
    previous_end = 0.0
    for original_index, segment in ordered:
        start = max(0.0, min(float(segment.start), source_duration))
        if start < previous_end:
            start = previous_end
        duration = max(0.0, min(float(segment.duration), source_duration - start))
        start = round(start, 3)
        duration = round(duration, 3)
        adjusted[original_index] = replace(segment, start=start, duration=duration)
        previous_end = round(start + duration, 3)
    return tuple(adjusted[index] for index in range(len(segments)))


def _requires_source_guided_longform_body(production_options: dict | None) -> bool:
    if not production_options:
        return False
    policy = str(production_options.get("target_duration_policy") or "source_guided")
    if policy in {"short_summary", "retain_core"}:
        return False
    visual_strategy = str(production_options.get("visual_asset_strategy") or "")
    return policy in {"source_guided", "keep_original"} or visual_strategy == "images2_contextual_inserts"


def _cold_open_segment_duration(source_duration: float, default_duration: float) -> float:
    if source_duration >= 120:
        return min(default_duration, 4.0)
    if source_duration >= 40:
        return min(default_duration, 3.5)
    return default_duration


def _target_creative_duration(source_duration: float) -> float:
    if source_duration < 120:
        return source_duration * 0.72
    if source_duration < 300:
        return max(120.0, source_duration * 0.78)
    return min(source_duration, max(300.0, source_duration * 0.9))


def _target_creative_duration_for_options(source_duration: float, production_options: dict | None) -> float:
    target_duration = _target_creative_duration(source_duration)
    if not production_options:
        return target_duration
    policy = str(production_options.get("target_duration_policy", "source_guided"))
    strength = str(production_options.get("creative_strength", "balanced"))
    if policy == "keep_original":
        target_duration = source_duration
    elif policy == "retain_core":
        target_duration = min(source_duration * 0.82, max(target_duration * 1.18, source_duration * 0.58))
    elif policy == "short_summary":
        target_duration = max(30.0, min(target_duration * 0.55, source_duration * 0.28))
    if strength == "strong" and policy != "short_summary":
        target_duration = min(source_duration, target_duration * 1.08)
    elif strength == "light":
        target_duration = max(20.0, target_duration * 0.88)
    return round(min(source_duration, target_duration), 3)


def _minimum_creative_longform_plan_duration(source_duration: float) -> float:
    if source_duration < 180:
        return 0.0
    if source_duration < 300:
        return min(180.0, max(96.0, source_duration * 0.36))
    return min(300.0, max(240.0, source_duration * 0.45))


def _creative_strategy(
    source_duration: float,
    profile: CreativeProfile,
    target_duration: float,
    target_segment_count: int,
    production_options: dict | None = None,
) -> CreativeStrategy:
    if source_duration >= 300:
        treatment = "director_longform_chapter_cut"
        version = "v5_director_longform"
    else:
        treatment = "chaptered_source_cut"
        version = "v5_director"
    moves = (
        "cold_open",
        "reset_context",
        "action_chain",
        "decision_point",
        "proof_close",
    )
    if profile.name == "food_social":
        moves = ("cold_open", "texture_pull", "action_chain", "payoff_close")
    return CreativeStrategy(
        version=version,
        target_duration=round(target_duration, 3),
        coverage_ratio=round(target_duration / source_duration, 4) if source_duration > 0 else 0.0,
        target_segment_count=target_segment_count,
        treatment=treatment,
        creative_moves=moves,
        production_notes=_production_notes_for_strategy(production_options),
    )


def _production_notes_for_strategy(production_options: dict | None) -> str:
    if not production_options:
        return ""
    return str(production_options.get("production_notes", "")).strip()[:500]


def _creative_move_for_semantic_role(semantic_role: str, first: bool) -> str:
    if first:
        return "cold_open"
    moves = {
        "tutorial_hook": "cold_open",
        "interface_state": "reset_context",
        "operation_step": "action_chain",
        "configuration_detail": "decision_point",
        "result_validation": "proof_close",
        "food_hook": "cold_open",
        "prep_action": "action_chain",
        "cook_transform": "action_chain",
        "texture_closeup": "texture_pull",
        "final_payoff": "payoff_close",
        "visual_hook": "cold_open",
        "action_moment": "action_chain",
        "decision_moment": "decision_point",
        "detail_moment": "texture_pull",
        "context_bridge": "reset_context",
        "result_moment": "proof_close",
    }
    return moves.get(semantic_role, "source_bridge")


def _segment_start(timestamp: float, duration: float, source_duration: float) -> float:
    return max(0.0, min(timestamp - duration * 0.45, source_duration - duration))


def _cold_open_segment_start(timestamp: float, duration: float, source_duration: float) -> float:
    return max(0.0, min(timestamp, source_duration - duration))


def _role_for_sample(sample: CreativeFrameSample, source_duration: float, score: float) -> str:
    if sample.timestamp >= source_duration * 0.6 and score >= 0.55:
        return "cover_candidate"
    if sample.timestamp >= source_duration * 0.72:
        return "finish_or_reveal"
    if sample.motion >= 0.52:
        return "process_action"
    if sample.sharpness >= 0.62 or sample.colorfulness >= 0.7:
        return "detail_closeup"
    return "context"


def _reason_for_role(role: str) -> str:
    reasons = {
        "cover_candidate": "色彩、清晰度和结果感更强，适合承担开场吸引力。",
        "finish_or_reveal": "靠近结尾且画面信息明确，适合做结果或收束。",
        "process_action": "运动变化明显，适合表现真实制作过程。",
        "detail_closeup": "细节更清晰，适合强化质感。",
        "context": "保留必要上下文，但不让它占用过长时长。",
    }
    return reasons.get(role, "保留真实视频信息。")


def _semantic_role_for_moment(
    profile: CreativeProfile,
    moment: CreativeMoment,
    sample: CreativeFrameSample,
    source_duration: float,
    first: bool,
    order: int,
    total_count: int,
) -> tuple[str, str]:
    if profile.name == "food_social":
        return _food_semantic_role(moment, sample, source_duration, first)
    if profile.name == "tutorial_screen":
        return _tutorial_semantic_role(moment, sample, source_duration, first, order, total_count)
    if source_duration >= 300 and total_count >= 10:
        return _generic_longform_semantic_role(moment, sample, source_duration, order, total_count)
    return _generic_semantic_role(moment, sample, source_duration, first)


def _food_semantic_role(
    moment: CreativeMoment,
    sample: CreativeFrameSample,
    source_duration: float,
    first: bool,
) -> tuple[str, str]:
    if first:
        return "food_hook", f"开场使用最高吸引力真实画面；visual_score={moment.score:.2f}"
    if sample.motion >= 0.58:
        return "prep_action", f"运动变化明显，适合表现制作动作；motion={sample.motion:.2f}"
    if sample.timestamp >= source_duration * 0.72:
        return "final_payoff", f"接近结尾，适合作为成品或收束；t={sample.timestamp:.1f}s"
    if sample.sharpness >= 0.68 or sample.colorfulness >= 0.82:
        return "texture_closeup", (
            f"清晰度或色彩更强，适合强调口感细节；sharpness={sample.sharpness:.2f},color={sample.colorfulness:.2f}"
        )
    return "cook_transform", f"保留制作过程中的状态变化；motion={sample.motion:.2f},color={sample.colorfulness:.2f}"


def _tutorial_semantic_role(
    moment: CreativeMoment,
    sample: CreativeFrameSample,
    source_duration: float,
    first: bool,
    order: int,
    total_count: int,
) -> tuple[str, str]:
    if first:
        return "tutorial_hook", f"开场先交代教程价值或结果预览；visual_score={moment.score:.2f}"
    if total_count >= 10:
        return _longform_tutorial_semantic_role(moment, sample, source_duration, order, total_count)
    if order == total_count - 1:
        return "result_validation", f"按教程结构用真实后段画面收束验证；t={sample.timestamp:.1f}s"
    if order == 1:
        return "interface_state", f"开场后保留界面基准状态，帮助观众建立操作环境；color={sample.colorfulness:.2f}"
    if total_count <= 4 and order == total_count - 2:
        return "configuration_detail", f"短摘要保留配置或关键页面变化，避免只剩操作片段；t={sample.timestamp:.1f}s"
    if order == 3 and source_duration >= 40:
        return "configuration_detail", f"按教程结构保留配置或关键页面变化；t={sample.timestamp:.1f}s"
    if sample.timestamp >= source_duration * 0.72:
        return "result_validation", f"靠近结尾，适合保留验证或结果画面；t={sample.timestamp:.1f}s"
    if sample.motion >= 0.30:
        return "operation_step", f"画面变化明显，可能对应点击、切换或操作步骤；motion={sample.motion:.2f}"
    if sample.timestamp >= source_duration * 0.45:
        return "configuration_detail", f"中后段稳定界面，适合保留配置细节；t={sample.timestamp:.1f}s"
    return "interface_state", f"低色彩稳定画面，适合作为关键界面状态；color={sample.colorfulness:.2f}"


def _longform_tutorial_semantic_role(
    moment: CreativeMoment,
    sample: CreativeFrameSample,
    source_duration: float,
    order: int,
    total_count: int,
) -> tuple[str, str]:
    role_arc = _longform_tutorial_role_arc(total_count)
    semantic_role = role_arc[min(order, len(role_arc) - 1)]
    evidence = {
        "tutorial_hook": f"长版教程先用高吸引力真实画面做钩子；visual_score={moment.score:.2f}",
        "interface_state": f"长版教程保留阶段界面基准，帮助观众跟上章节；t={sample.timestamp:.1f}s",
        "operation_step": f"长版教程分散保留核心操作步骤；motion={sample.motion:.2f},t={sample.timestamp:.1f}s",
        "configuration_detail": f"长版教程保留配置、参数或关键页面变化；t={sample.timestamp:.1f}s",
        "result_validation": f"长版教程用后段真实画面收束验证；t={sample.timestamp:.1f}s",
    }
    return semantic_role, evidence.get(semantic_role, f"长版教程保留真实章节信息；t={sample.timestamp:.1f}s")


def _generic_semantic_role(
    moment: CreativeMoment,
    sample: CreativeFrameSample,
    source_duration: float,
    first: bool,
) -> tuple[str, str]:
    if first:
        return "visual_hook", f"开场选择视觉分最高的真实片段；visual_score={moment.score:.2f}"
    if sample.timestamp >= source_duration * 0.72:
        return "result_moment", f"靠近结尾，适合作为结果或收束；t={sample.timestamp:.1f}s"
    if sample.motion >= 0.45:
        return "action_moment", f"运动变化明显；motion={sample.motion:.2f}"
    if sample.sharpness >= 0.62 or sample.colorfulness >= 0.65:
        return "detail_moment", f"细节或色彩较强；sharpness={sample.sharpness:.2f},color={sample.colorfulness:.2f}"
    return "context_bridge", f"补足上下文但不作为主要卖点；score={moment.score:.2f}"


def _generic_longform_semantic_role(
    moment: CreativeMoment,
    sample: CreativeFrameSample,
    source_duration: float,
    order: int,
    total_count: int,
) -> tuple[str, str]:
    role_arc = _longform_generic_role_arc(total_count)
    semantic_role = role_arc[min(order, len(role_arc) - 1)]
    evidence = {
        "visual_hook": f"长视频先用高吸引力真实画面做钩子；visual_score={moment.score:.2f}",
        "context_bridge": f"长视频补足事件上下文，避免动作堆叠；t={sample.timestamp:.1f}s",
        "action_moment": f"长视频分散保留真实动作或事件推进；motion={sample.motion:.2f},t={sample.timestamp:.1f}s",
        "decision_moment": f"长视频保留判断点或情绪转折，形成段落变化；t={sample.timestamp:.1f}s",
        "detail_moment": (
            f"长视频穿插细节/反应画面，降低同类动作重复；sharpness={sample.sharpness:.2f},"
            f"color={sample.colorfulness:.2f}"
        ),
        "result_moment": f"长视频用后段真实画面收束结果或情绪；t={sample.timestamp:.1f}s",
    }
    return semantic_role, evidence.get(semantic_role, f"长视频保留真实章节信息；t={sample.timestamp:.1f}s")


def _purpose_for_semantic_role(semantic_role: str, role_evidence: str, first: bool) -> str:
    lead = {
        "food_hook": "先用高吸引力成品/细节开场，建立食欲和结果感。",
        "prep_action": "保留真实制作动作，让观众看到过程推进。",
        "cook_transform": "保留食材或画面状态变化，避免只看静态结果。",
        "texture_closeup": "推近质感细节，提高真实食物吸引力。",
        "final_payoff": "用成品或收束画面形成观看闭环。",
        "tutorial_hook": "先交代教程价值或最终结果，避免平铺直叙。",
        "interface_state": "保留关键界面状态，帮助观众理解操作环境。",
        "operation_step": "保留真实操作变化，让教程链路不断裂。",
        "configuration_detail": "保留配置、参数或关键选项，避免教程失真。",
        "result_validation": "保留验证或结果画面，让教程有可信收束。",
        "visual_hook": "先用视觉更强的真实片段建立观看入口。",
        "action_moment": "保留真实动作或事件，提高片段密度。",
        "decision_moment": "保留判断点或情绪转折，让叙事不只是一串动作。",
        "detail_moment": "保留细节画面，提升真实质感。",
        "context_bridge": "补足上下文，避免剪辑断裂。",
        "result_moment": "保留结果或结尾收束。",
    }.get(semantic_role, "保留真实视频信息。")
    if first and not semantic_role.endswith("hook"):
        lead = "先用当前最强真实画面开场。" + lead
    return f"{lead}{role_evidence}"


def _purpose_for_moment(moment: CreativeMoment, first: bool) -> str:
    if first:
        return f"先用高吸引力成品/细节开场，避免平铺直叙；{moment.reason}"
    if moment.role == "process_action":
        return f"保留真实动作推进，让观众看到制作过程；{moment.reason}"
    if moment.role in {"cover_candidate", "finish_or_reveal"}:
        return f"用结果画面收束，形成更完整的观看闭环；{moment.reason}"
    if moment.role == "detail_closeup":
        return f"推近质感细节，提高真实食物吸引力；{moment.reason}"
    return f"补足上下文，避免剪辑断裂；{moment.reason}"


def _estimate_sharpness(luma: Image.Image) -> float:
    resized = luma.resize((96, 96))
    pixels = resized.load()
    width, height = resized.size
    total = 0.0
    count = 0
    for y in range(1, height):
        for x in range(1, width):
            total += abs(pixels[x, y] - pixels[x - 1, y])
            total += abs(pixels[x, y] - pixels[x, y - 1])
            count += 2
    if count == 0:
        return 0.0
    return round(_clamp01((total / count) / 38.0), 4)


def _average_feature(samples: Sequence[CreativeFrameSample], name: str) -> float:
    if not samples:
        return 0.0
    return sum(float(getattr(sample, name)) for sample in samples) / len(samples)


def _clamp01(value: float) -> float:
    if math.isnan(value):
        return 0.0
    return max(0.0, min(1.0, value))


def _format_seconds(seconds: float) -> str:
    value = f"{seconds:.3f}".rstrip("0").rstrip(".")
    return f"{value}s"
