"""主时间轴（P16）：配音定稿后立刻产出逐句真实起止时间，作全链唯一时钟。

设计要点（用户拍板的架构升级第一期）：
- 病根：whisper 对齐此前发生在字幕阶段（最后一站），特效的时间全靠字符占比估算，
  导致"字幕对得上音频、特效对不上"。本期把对齐前移到 assemble 产配音之后，逐句生成
  timeline.json，effects/subtitles 共同消费它——全链只有一个时钟。
- 时间戳来源（本期）：whisper 对齐，直接复用 subtitles.build_cues 的 "align" 模式。
  净成本为零：字幕阶段消费本文件后不再自跑 ASR（见 subtitles 的 timeline 模式）。
- 二期升级口：TTS 原生字级时间戳（火山 with_timestamp）可无缝替换本文件的时间来源，
  produce_timeline / load_timeline 的接口不变，下游 effects/subtitles 零改动。
- 时间轴是增强件，绝不阻断成片：任何失败都返回 None 并 print 中文告警。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

Runner = Callable[..., subprocess.CompletedProcess]

TIMELINE_VERSION = "timeline_v1"
TIMELINE_FILENAME = "timeline.json"


def produce_timeline(
    voiceover_path: Path | str,
    full_text: str,
    output_dir: Path | str,
    runner: Runner = subprocess.run,
) -> Path | None:
    """对配音音频做 whisper 对齐，逐句产出 output_dir/timeline.json，成功返回其路径。

    复用 subtitles 的现成机器：split_sentences 把原稿分句 → build_cues 以 "align" 模式
    对齐到配音的真实时间线（逐条 Cue 转录为 sentence）。build_cues align 失败会自己抛异常，
    这里捕获所有异常返回 None 并留中文告警——时间轴是增强件，绝不阻断成片。
    """
    try:
        # 惰性导入 subtitles（较重，且便于测试 monkeypatch build_cues）。
        from video_factory.subtitles import build_cues, split_sentences

        audio = Path(voiceover_path)
        if not audio.exists():
            print(f"主时间轴跳过：配音音频不存在（{audio}），特效/字幕将回落估算。")
            return None
        sentences = split_sentences(full_text)
        # mode="align"：以 ASR 时间线对齐原稿；失败它自己抛，由下面的 except 兜底降级为 None。
        cues, _used_mode, warnings = build_cues(sentences, "align", audio, runner)
        for warning in warnings:
            print(f"主时间轴告警：{warning}")
        payload = {
            "version": TIMELINE_VERSION,
            "sentences": [
                {"text": cue.text, "start": cue.start, "end": cue.end} for cue in cues
            ],
        }
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / TIMELINE_FILENAME
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return out_path
    except Exception as exc:  # noqa: BLE001 - 时间轴任何失败都不得阻断成片，一律降级
        print(f"主时间轴生成失败，特效/字幕将回落估算：{exc}")
        return None


def load_timeline(path: Path | str) -> list[dict] | None:
    """读取 timeline.json 的 sentences 列表；文件缺失/损坏/结构异常一律返回 None。

    返回的每条句子归一化为 {"text": str, "start": float, "end": float}，
    供 effects/subtitles 直接消费。任何解析异常都当作"无时间轴"，下游自行回落估算。
    """
    try:
        p = Path(path)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        sentences = data.get("sentences")
        if not isinstance(sentences, list):
            return None
        result: list[dict] = []
        for item in sentences:
            if not isinstance(item, dict):
                return None
            result.append(
                {
                    "text": str(item.get("text") or ""),
                    "start": float(item.get("start") or 0.0),
                    "end": float(item.get("end") or 0.0),
                }
            )
        return result
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
