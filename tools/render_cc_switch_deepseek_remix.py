import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from video_factory.cc_switch_deepseek_remix import (
    DEFAULT_OUTPUT_DIR,
    render_cc_switch_deepseek_remix,
)


def main() -> None:
    artifacts = render_cc_switch_deepseek_remix(Path(DEFAULT_OUTPUT_DIR))
    print(f"Rendered CC Switch DeepSeek remix: {artifacts['video']}")
    print(f"Cover: {artifacts['cover']}")
    print(f"Contact sheet: {artifacts['contact_sheet']}")
    print(f"Report: {artifacts['report']}")


if __name__ == "__main__":
    main()
