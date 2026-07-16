"""特效音（sound effects）：为 Remotion 视觉特效在其出现时刻配一声音效，让转场更自然。

定位：**可选、可替换**的旁挂能力。特效音与视觉特效一一对应（片头标题划入配 whoosh、
章节卡弹出配 pop、底部花字条滑入配 swoosh），时间点直接取自 effects_manifest，
不需要任何智能判断——由 effects.overlay_effects 在叠加时按 start 混进音轨。

音效来源：内置一小包 ffmpeg 合成的音效（whoosh/pop/swoosh），提交进仓库开箱即用。
**可替换**：把同名 wav 放进 assets/sfx/ 覆盖即可换成你自己的 CC0 录音级音效。
缺文件不阻断成片——找不到某个音效就跳过那一声。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable

Runner = Callable[..., subprocess.CompletedProcess]

# 内置音效包目录（随仓库发布）；用户放同名文件即可覆盖。
DEFAULT_SFX_DIR = Path(__file__).resolve().parent / "assets" / "sfx"

# 特效类型 → 音效文件名。键与 effects_manifest 的 type 对齐。
SFX_BY_TYPE = {
    "intro": "whoosh.wav",
    # chapter_card 无音效（2026-07-15 用户点名去掉章节文字出现时的"啵"声）：
    # 不在映射里 = resolve_sfx_path 返回 None = 静默跳过，章节大字纯视觉浮现。
    "lower_third": "swoosh.wav",
    "key_points": "whoosh.wav",
    "quote_card": "pop.wav",
    "number_pop": "pop.wav",
    # 荧光笔高亮：一声"刷"贴合金笔划过；打字机引用与金句卡同声起势。
    "highlight_sweep": "swoosh.wav",
    "typewriter_quote": "pop.wav",
    # 关键词弹出复用"啵"的 pop；冷开场卡用 whoosh 起势；金句全屏卡用低频冲击音。
    "keyword_pop": "pop.wav",
    "opening_card": "whoosh.wav",
    "golden_card": "impact.wav",
    # 金句开屏三行式（黑卡退役后的形态）：起点一声低频冲击（不逐行——逐行"刷刷"
    # 只认 hook_opener，见 effects._build_sfx_audio 特判）。
    "golden_lines": "impact.wav",
    # 开屏钩子序列（替代已退役的冷开场卡）：首行 whoosh 起势；逐行"刷刷"由
    # effects._build_sfx_audio 按 offsets 特判注入（首行 whoosh、后续行 swoosh，
    # 2026-07-15 用户定案），本映射仅作 offsets 缺失时的兜底首声。
    "hook_opener": "whoosh.wav",
}

DEFAULT_SFX_VOLUME = 0.35  # 混音时相对主口播的音量（0~1，宁小勿大，避免盖住人声）

# 每个合成音效的 ffmpeg -filter 参数（源都是 anoisesrc/sine，44100 单声道 s16，短促）。
# 有意做得克制：轻扫过声/短弹出声，只为让转场"有落点"，不喧宾夺主。
_SYNTH_SPECS = {
    "whoosh.wav": (
        "anoisesrc=d=0.55:c=pink:r=44100:a=0.5",
        "bandpass=f=1200:width_type=h:w=1600,afade=t=in:d=0.25,"
        "afade=t=out:st=0.3:d=0.25,volume=2.0,"
        "aformat=sample_fmts=s16:channel_layouts=mono",
    ),
    "pop.wav": (
        # 章节卡"弹出"声：两段短促正弦叠一点高频，快起快落，做成"啵"的颗粒感而非纯电音；
        # 音量抬到接近 whoosh，否则在口播高峰处会被完全盖住（实测 volume=0.7 时几乎听不见）。
        "sine=frequency=680:duration=0.13:r=44100",
        "afade=t=in:d=0.004,afade=t=out:st=0.025:d=0.1,volume=1.6,"
        "aformat=sample_fmts=s16:channel_layouts=mono",
    ),
    "swoosh.wav": (
        "anoisesrc=d=0.32:c=pink:r=44100:a=0.4",
        "bandpass=f=2000:width_type=h:w=2500,afade=t=in:d=0.1,"
        "afade=t=out:st=0.16:d=0.15,volume=1.8,"
        "aformat=sample_fmts=s16:channel_layouts=mono",
    ),
    "impact.wav": (
        # 金句卡冲击音：低频 boom —— 80Hz 正弦 0.28s，快起慢落，
        # 造出鼓锤落地的冲击感，给全屏金句卡"砸"出一个落点。
        "sine=frequency=80:duration=0.28:r=44100",
        "afade=t=in:d=0.008,afade=t=out:st=0.10:d=0.17,"
        "volume=3.0,aformat=sample_fmts=s16:channel_layouts=mono",
    ),
}


class SfxError(RuntimeError):
    pass


def resolve_sfx_path(effect_type: str, sfx_dir: Path | str | None = None) -> Path | None:
    """特效类型 → 音效文件路径。目录/文件缺失返回 None（调用方跳过这一声，不阻断）。"""
    filename = SFX_BY_TYPE.get(str(effect_type))
    if not filename:
        return None
    base = Path(sfx_dir) if sfx_dir else DEFAULT_SFX_DIR
    path = base / filename
    return path if path.exists() else None


def resolve_sfx_file(filename: str, sfx_dir: Path | str | None = None) -> Path | None:
    """按文件名直接解析音效路径（供逐行注入等绕过类型映射的场景）。缺失返回 None。"""
    if not filename:
        return None
    base = Path(sfx_dir) if sfx_dir else DEFAULT_SFX_DIR
    path = base / filename
    return path if path.exists() else None


def ensure_default_pack(sfx_dir: Path | str | None = None, runner: Runner = subprocess.run) -> list[Path]:
    """合成缺失的内置音效到 sfx_dir（已存在的不覆盖，尊重用户替换）。返回本次生成的文件列表。

    ffmpeg 缺失时抛 SfxError（调用方可吞掉：没有音效包只是没特效音，不阻断成片）。
    """
    base = Path(sfx_dir) if sfx_dir else DEFAULT_SFX_DIR
    if shutil.which("ffmpeg") is None:
        raise SfxError("未找到 ffmpeg，无法合成内置音效包。")
    base.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    for filename, (source, filt) in _SYNTH_SPECS.items():
        out = base / filename
        if out.exists():
            continue
        command = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", source,
            "-af", filt, str(out),
        ]
        completed = runner(command, check=False, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if getattr(completed, "returncode", 0) != 0 or not out.exists():
            stderr = (getattr(completed, "stderr", "") or "")[:300]
            raise SfxError(f"合成音效 {filename} 失败：{stderr}")
        generated.append(out)
    return generated


def main(argv: list[str] | None = None) -> int:
    """命令行入口：生成/补齐内置音效包。用于发布前预生成或用户重建。"""
    generated = ensure_default_pack()
    if generated:
        print(f"已合成 {len(generated)} 个音效：" + "、".join(p.name for p in generated))
    else:
        print("内置音效包已齐全，无需生成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
