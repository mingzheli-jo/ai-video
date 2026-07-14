"""素材池扫描 + 时长分配。

流程定位（批量生产 P2 上游）：用户给一个素材目录（若干视频文件），先逐个
ffprobe 探测时长和分辨率组成素材池，再按 rewrite.json 各小节的字数占比把目标
时长切成若干段，并从素材池顺序轮转地取片段。纯分配逻辑与 ffprobe 探测解耦，
方便测试用假 runner 注入。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

Runner = Callable[..., subprocess.CompletedProcess]

# 支持的素材扩展名（与 source_download._find_downloaded_video 口径一致）。
VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm", ".m4v")
# 图片素材（豆包生图/用户自备）：拼装时经 zoompan 运镜转成动态片段（见 assemble）。
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
# 图片没有物理时长，按虚拟时长参与分配轮转：每次被取用最多贡献这么多秒
# （切片会配不同运镜，同图多次出现观感不同，不至于一张图霸屏全节）。
IMAGE_VIRTUAL_DURATION = 6.0
# 每小节最短时长保护：太短的镜头切出来观感碎，硬性抬到这个下限。
MIN_SECTION_SECONDS = 2.0


class AssetPoolError(RuntimeError):
    pass


@dataclass(frozen=True)
class AssetClip:
    path: Path
    duration: float
    width: int
    height: int
    is_image: bool = False


@dataclass(frozen=True)
class ClipSlice:
    """一节内部引用的一段素材切片。"""

    path: Path
    start: float
    duration: float


@dataclass(frozen=True)
class SectionAllocation:
    index: int
    duration: float
    slices: tuple[ClipSlice, ...]


@dataclass(frozen=True)
class ScanResult:
    clips: tuple[AssetClip, ...]
    warnings: tuple[str, ...]


def scan_asset_pool(directory: Path | str, runner: Runner = subprocess.run) -> ScanResult:
    """遍历目录下的视频文件，逐个 ffprobe 取时长/分辨率。

    单个文件探测失败只跳过并记入 warnings，不中断整个扫描；目录不存在或最终
    没有任何可用素材时抛 AssetPoolError。
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise AssetPoolError(f"素材目录不存在或不是目录：{directory}")

    clips: list[AssetClip] = []
    warnings: list[str] = []
    for path in _iter_video_files(directory):
        try:
            clip = _probe_clip(path, runner)
        except AssetPoolError as exc:
            warnings.append(str(exc))
            continue
        clips.append(clip)

    if not clips:
        supported = ", ".join(VIDEO_SUFFIXES + IMAGE_SUFFIXES)
        raise AssetPoolError(
            f"素材目录里没有可用的视频/图片素材（支持 {supported}）：{directory}"
        )
    return ScanResult(clips=tuple(clips), warnings=tuple(warnings))


def _iter_video_files(directory: Path) -> list[Path]:
    allowed = VIDEO_SUFFIXES + IMAGE_SUFFIXES
    files = [
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in allowed
    ]
    return sorted(files, key=lambda path: path.name)


def _probe_clip(path: Path, runner: Runner) -> AssetClip:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        completed = runner(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise AssetPoolError(f"探测素材失败（{path.name}）：{exc}") from exc
    if getattr(completed, "returncode", 0) != 0:
        raise AssetPoolError(f"ffprobe 无法解析素材，已跳过：{path.name}")

    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise AssetPoolError(f"ffprobe 输出不是合法 JSON，已跳过：{path.name}") from exc

    width, height = _extract_resolution(payload)
    if path.suffix.lower() in IMAGE_SUFFIXES:
        # 图片：无物理时长，按虚拟时长参与轮转；分辨率必须探到（zoompan 需要）。
        if width <= 0 or height <= 0:
            raise AssetPoolError(f"图片分辨率探测失败，已跳过：{path.name}")
        return AssetClip(
            path=path, duration=IMAGE_VIRTUAL_DURATION,
            width=width, height=height, is_image=True,
        )
    duration = _extract_duration(payload)
    if duration <= 0:
        raise AssetPoolError(f"素材时长无效，已跳过：{path.name}")
    return AssetClip(path=path, duration=duration, width=width, height=height)


def _extract_duration(payload: dict) -> float:
    raw = (payload.get("format") or {}).get("duration")
    if raw is None:
        for stream in payload.get("streams") or []:
            if stream.get("codec_type") == "video" and stream.get("duration"):
                raw = stream.get("duration")
                break
    try:
        return round(float(raw), 3)
    except (TypeError, ValueError):
        return 0.0


def _extract_resolution(payload: dict) -> tuple[int, int]:
    for stream in payload.get("streams") or []:
        if stream.get("codec_type") == "video":
            return _safe_int(stream.get("width")), _safe_int(stream.get("height"))
    return 0, 0


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def allocate_sections_to_clips(
    sections_char_counts: Sequence[int],
    clips: Sequence[AssetClip],
    total_duration: float,
) -> list[SectionAllocation]:
    """把 total_duration 按各节字数占比切分，再从素材池顺序轮转取片段。

    - 总秒数精确等于 total_duration（浮点残差全部归到最后一节）。
    - 每节至少 MIN_SECTION_SECONDS 秒。
    - 素材按顺序轮转分配；同一素材被多节复用时用已消耗偏移量错开起点，
      素材本身放不下时从头回绕（简单去重策略，避免连续画面重复）。
    """
    if not clips:
        raise AssetPoolError("没有可用素材，无法分配片段。")
    if total_duration <= 0:
        raise AssetPoolError("目标时长必须为正数。")

    durations = _split_durations(sections_char_counts, total_duration)
    offsets = {clip.path: 0.0 for clip in clips}
    cursor = 0
    allocations: list[SectionAllocation] = []
    for index, section_duration in enumerate(durations):
        slices, cursor = _fill_section(section_duration, clips, offsets, cursor)
        # duration 保持未四舍五入，确保 sum(durations) 精确等于 total_duration。
        allocations.append(
            SectionAllocation(index=index, duration=section_duration, slices=tuple(slices))
        )
    return allocations


def _split_durations(sections_char_counts: Sequence[int], total_duration: float) -> list[float]:
    counts = [max(0, int(count)) for count in sections_char_counts]
    if not counts:
        raise AssetPoolError("没有可分配的小节。")
    section_count = len(counts)
    if total_duration < MIN_SECTION_SECONDS * section_count:
        raise AssetPoolError(
            f"目标时长 {total_duration:.1f}s 太短：{section_count} 节每节至少 {MIN_SECTION_SECONDS}s。"
        )

    total_chars = sum(counts)
    floor = MIN_SECTION_SECONDS * section_count
    flexible = total_duration - floor
    if total_chars <= 0:
        shares = [flexible / section_count] * section_count
    else:
        shares = [flexible * count / total_chars for count in counts]
    durations = [MIN_SECTION_SECONDS + share for share in shares]
    # 浮点残差归到最后一节，保证 sum(durations) == total_duration。
    durations[-1] += total_duration - sum(durations)
    return durations


def _fill_section(
    section_duration: float,
    clips: Sequence[AssetClip],
    offsets: dict[Path, float],
    cursor: int,
) -> tuple[list[ClipSlice], int]:
    slices: list[ClipSlice] = []
    remaining = section_duration
    guard = 0
    max_iterations = len(clips) * 4 + 8
    while remaining > 1e-3 and guard < max_iterations:
        guard += 1
        clip = clips[cursor % len(clips)]
        cursor += 1
        start = offsets[clip.path]
        available = clip.duration - start
        if available <= 1e-3:
            offsets[clip.path] = 0.0  # 用完回绕，下轮从头再取。
            start = 0.0
            available = clip.duration
        take = min(available, remaining)
        slices.append(ClipSlice(path=clip.path, start=round(start, 3), duration=round(take, 3)))
        offsets[clip.path] = round(start + take, 3)
        remaining -= take
    return slices, cursor
