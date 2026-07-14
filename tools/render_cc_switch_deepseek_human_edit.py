import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from video_factory.cc_switch_deepseek_human_edit import (
    DEFAULT_OUTPUT_DIR,
    render_cc_switch_deepseek_human_edit,
)


def main() -> None:
    artifacts = render_cc_switch_deepseek_human_edit(Path(DEFAULT_OUTPUT_DIR))
    print(f"Rendered human edit video: {artifacts['video']}")
    print(f"Cover: {artifacts['cover']}")
    print(f"Contact sheet: {artifacts['contact_sheet']}")
    print(f"Edit decision list: {artifacts['edl']}")
    print(f"Report: {artifacts['report']}")


if __name__ == "__main__":
    main()
