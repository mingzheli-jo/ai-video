import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from video_factory.quality_lab import DEFAULT_OUTPUT_DIR, render_quality_lab_sample


def main() -> None:
    artifacts = render_quality_lab_sample(Path(DEFAULT_OUTPUT_DIR))
    print(f"Rendered sample video: {artifacts['video']}")
    print(f"Cover: {artifacts['cover']}")
    print(f"Report: {artifacts['report']}")


if __name__ == "__main__":
    main()
