"""Shared CJK-capable font loading.

历史上 replicate.py / pipeline.py / original.py / reference_guided_original.py
各自维护了一份几乎相同的 `_font()`，候选列表和粗体处理略有出入，导致同一个
"加粗字体选错 face" 的 bug 只在其中一份里被修过。这里统一成单一实现，各文件的
`_font()` / `_caption_font()` 退化为薄封装。

候选表用 (路径, 粗体时的 face 索引) 表达不同平台的粗体策略：
- Windows：靠文件名区分粗细（msyhbd vs msyh、Arial Bold vs Arial），face 索引恒为 0。
- macOS PingFang.ttc：单文件多 face，粗体取 face 索引 1。
非粗体请求一律用 face 索引 0。找不到任何字体时回退到 Pillow 默认位图字体。
"""

from __future__ import annotations

from PIL import ImageFont


def _font_candidates(bold: bool) -> list[tuple[str, int]]:
    return [
        # Windows：优先用中文字体，粗细由文件名决定。
        ("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc", 0),
        ("C:/Windows/Fonts/simhei.ttf", 0),
        # macOS：PingFang 用 face 索引选粗体，其余为单一字重。
        ("/System/Library/Fonts/PingFang.ttc", 1),
        ("/System/Library/Fonts/STHeiti Light.ttc", 0),
        ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
        ("/Library/Fonts/Arial Unicode.ttf", 0),
        (
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
            if bold
            else "/System/Library/Fonts/Supplemental/Arial.ttf",
            0,
        ),
    ]


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path, bold_index in _font_candidates(bold):
        try:
            return ImageFont.truetype(path, size=size, index=bold_index if bold else 0)
        except OSError:
            continue
    return ImageFont.load_default()
