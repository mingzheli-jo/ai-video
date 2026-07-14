from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .frames import render_contact_sheet, render_sample_frames
from .media import build_output_slug, probe_media
from .models import AnalysisPaths, AnalysisResult
from .reports import write_report_artifacts


def analyze_reference_video(
    source_path: Path,
    output_dir: Optional[Path] = None,
    sample_count: int = 8,
    overwrite_reports: bool = False,
) -> AnalysisResult:
    media = probe_media(source_path)
    resolved_output = output_dir or Path("video_factory/output/analysis") / build_output_slug(
        source_path
    )
    paths = AnalysisPaths.for_output_dir(resolved_output)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.sample_frames_dir.mkdir(parents=True, exist_ok=True)

    paths.media_info.write_text(
        json.dumps(media.to_json_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    render_contact_sheet(media, paths.contact_sheet)
    frames = render_sample_frames(media, paths.sample_frames_dir, count=sample_count)
    write_report_artifacts(media, frames, paths, overwrite=overwrite_reports)
    return AnalysisResult(media=media, paths=paths, sample_frames=frames)
