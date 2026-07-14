import argparse
from pathlib import Path
from typing import Optional

from .runner import analyze_reference_video


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze a local reference video for production quality."
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Local reference video file to analyze.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Directory for analysis artifacts; defaults to "
            "video_factory/output/analysis/<slug>."
        ),
    )
    parser.add_argument(
        "--sample-count",
        type=int,
        default=8,
        help="Number of review frames to extract; default is 8.",
    )
    parser.add_argument(
        "--overwrite-reports",
        action="store_true",
        help="Overwrite existing markdown reports instead of preserving expert edits.",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    result = analyze_reference_video(
        args.input,
        output_dir=args.output,
        sample_count=args.sample_count,
        overwrite_reports=args.overwrite_reports,
    )

    print(f"media_info: {result.paths.media_info}")
    print(f"contact_sheet: {result.paths.contact_sheet}")
    print(f"timeline: {result.paths.timeline}")
    print(f"quality_report: {result.paths.quality_report}")
    print(f"production_template: {result.paths.production_template}")
    print(f"scorecard: {result.paths.scorecard}")


if __name__ == "__main__":
    main()
