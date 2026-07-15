"""配音阶段（P16 主时间轴第二期，用户拍板）：TTS 前移为独立阶段 + 主时间轴产出。

流程定位（P16 二期）：此前 TTS 合成埋在 assemble 内部，导致主时间轴（逐句真实起止）
要等到拼装阶段才生成——生图的变长拍规划（卡话切）拿不到时间轴。本阶段把 assemble 的
「配音获取 + 时间轴产出」整段前移，夹在 rewrite 与 image_gen 之间：

- 配音获取：--audio 给了就直接引用现成配音；否则从 rewrite 的 full_voiceover 现场 TTS 合成，
  未显式 voice_speed 时再按 atempo 微调（与 assemble 完全同款——**直接复用 assemble 的
  _synthesize_from_rewrite / _fit_audio_to_target**，零重复实现，杜绝口径漂移）。
- 时间轴产出：配音定稿后立刻调 timeline.produce_timeline（whisper 对齐，逐句真实起止），
  产出 output/timeline.json，供 image_gen 变长拍 + assemble/effects/subtitles 共同消费。

降级纪律：TTS 失败按现有异常上抛、记 failed（配音是成片刚需）；时间轴是增强件，任何
失败只告警不阻断（assemble 侧仍会兜底再产一次）。凭据经 credentials_store 补齐；既无
--audio 也无 --tts 时按 assemble 同款语义留空（silent 成片路径），跳过合成与时间轴。
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from video_factory import credentials_store, stage_report, timeline
from video_factory.assemble import (
    AssemblyError,
    _fit_audio_to_target,
    _load_rewrite,
    _probe_duration,
    _resolve_target_duration,
    _synthesize_from_rewrite,
)
from video_factory.pipeline import TTSProviderError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m video_factory.voice",
        description="配音阶段：现成配音复用 / 现场 TTS 合成 + 主时间轴产出（timeline.json）",
    )
    parser.add_argument("--rewrite", required=True, help="rewrite.json 路径（P1 产出）")
    parser.add_argument(
        "--output", default="video_factory/output/voice", help="输出目录（批量下即 job 目录）"
    )
    audio_group = parser.add_mutually_exclusive_group()
    audio_group.add_argument("--audio", default="", help="已有配音文件（与 --tts 互斥）")
    audio_group.add_argument(
        "--tts",
        default="",
        choices=["doubao", "openai", "edge"],
        help="从 rewrite 的 full_voiceover 现场合成配音（与 --audio 互斥）",
    )
    parser.add_argument("--voice", default="", help="TTS 音色（可选）")
    parser.add_argument(
        "--voice-speed",
        dest="voice_speed",
        type=float,
        default=None,
        help="配音语速 0.5~2.0（1.0=原速；仅 --tts 现场合成生效）。显式设语速后跳过 atempo "
        "时长微调——分镜/切屏改以实测配音时长为轴，节奏完全跟随语速",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="目标秒数（atempo 微调基准，覆盖 rewrite 里的目标时长；语义同 assemble 同名参数）",
    )
    args = parser.parse_args(argv)
    # 补齐凭据（TTS 的 VOLC_* 等）：credentials.yaml → 空缺的环境变量（真实 env 优先）。
    credentials_store.ensure_env_loaded()

    output_dir = Path(args.output)
    try:
        rewrite = _load_rewrite(Path(args.rewrite))
        # 配音获取（照搬 assemble.main）：--audio 优先直接引用；否则现场 TTS + 可选 atempo 微调。
        audio_path: Path | None = None
        if args.audio:
            audio_path = Path(args.audio)
            if not audio_path.exists():
                raise AssemblyError(f"配音文件不存在：{audio_path}")
        elif args.tts:
            # 先清掉上一轮可能遗留的 voiceover_fitted.wav：本轮若微调未触发（偏差在容差内）
            # 就不会重写它，而 build_assemble_argv 按盘优先取 fitted——陈旧 fitted 会被误选、
            # 与本轮新配音系统性错位。故重合成前先删，保证盘上 fitted 永远对应本轮音轨。
            stale_fitted = output_dir / "voiceover_fitted.wav"
            try:
                stale_fitted.unlink()
            except OSError:  # 含 FileNotFoundError：不存在即无需清
                pass
            audio_path = _synthesize_from_rewrite(
                rewrite, args.tts, args.voice, output_dir, subprocess.run,
                speed=args.voice_speed,
            )
            if args.voice_speed is None:
                # 时长闭环第二级：现场合成的配音偏离目标 >5% 时 atempo 微调（0.9~1.1）。
                # 显式设了语速则让位（微调会抵消语速意图），分镜/切屏以实测时长为轴。
                fit_target = _resolve_target_duration(rewrite, args.duration)
                audio_path, fit_note = _fit_audio_to_target(
                    Path(audio_path), fit_target, output_dir, subprocess.run
                )
                if fit_note:
                    print(f"时长微调：{fit_note}")
        # else：既无 --audio 也无 --tts —— 与 assemble 同款语义留空（silent 成片路径）。
    except (AssemblyError, TTSProviderError, OSError) as exc:
        print(f"配音失败：{exc}")
        stage_report.write_stage_error(args.output, "voice", f"配音失败：{exc}")
        return 1

    # 主时间轴（P16）：配音定稿后立刻对齐产出 timeline.json，作全链唯一时钟。
    # 对齐的是最终混进成片的那条音轨（fitted 与否都对）；失败只告警不阻断（增强件）。
    timeline_made = False
    if audio_path is not None:
        full_voiceover = str(rewrite.get("full_voiceover") or "").strip()
        if full_voiceover:
            if timeline.produce_timeline(audio_path, full_voiceover, output_dir) is not None:
                timeline_made = True
            else:
                print("主时间轴未生成（对齐失败或依赖缺失），生图/特效/字幕将回落估算。")
        else:
            print("主时间轴跳过：rewrite 缺 full_voiceover，无法对齐。")
    else:
        print("配音跳过：未提供 --audio 且未指定 --tts，成片将无配音（silent）。")

    if audio_path is not None:
        actual = _probe_duration(Path(audio_path), subprocess.run)
        dur_text = f"{actual:.1f}s" if actual > 0 else "未知"
        print(
            f"配音完成：音轨 {audio_path}（时长 {dur_text}），"
            f"主时间轴{'已产出 timeline.json' if timeline_made else '未产出'}。"
        )
    else:
        print("配音完成：无配音音轨，主时间轴未产出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
