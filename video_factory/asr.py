"""视频/音频 → 语音转写（ASR）。

与 llm.py / pipeline.py 的 provider 层同一风格：ffmpeg 提音、urllib 直调、
凭据只从环境变量读取、错误统一包装成带排查提示的 ASRError。

参数选择沿用生产验证过的实现（16kHz 单声道提音、initial_prompt 偏置中文术语、
faster-whisper int8/vad_filter），见项目 README 与 transcribe 参考实现。

顶层绝不 import faster_whisper：未安装时应给出 `pip install -e ".[asr]"` 提示，
而不是在导入本模块时就崩溃。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

FASTER_WHISPER_DEFAULT_MODEL = "small"
OPENAI_DEFAULT_MODEL = "gpt-4o-mini-transcribe"
OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"

# 云端上传体积上限（OpenAI 转写接口约 25MB，留 1MB 余量按 24MB 分块）。
OPENAI_MAX_UPLOAD_BYTES = 24 * 1024 * 1024
OPENAI_CHUNK_SECONDS = 600
# 32kbps mp3 ≈ 4000 字节/秒，用于提取前预估体积，超限直接走分块，
# 避免先整段提取再丢弃重提一遍（长视频会白跑一次全长转码）。
MP3_BYTES_PER_SECOND = 4000

# 偏置识别器认知这位交易博主的中文术语，显著提升专有名词准确率。
DEFAULT_INITIAL_PROMPT = (
    "以下是一位短线交易博主的炒股口播。涉及股票、A股、短线、仓位管理、"
    "止盈止损、收益风险比、胜率、盈亏比、概率、板块、个股、技术指标、"
    "均线、MACD、成交量、结构牛、市场转折、高股息、防守板块、认知、心态。"
)


class ASRError(RuntimeError):
    pass


@dataclass(frozen=True)
class ASRConfig:
    provider: str = "auto"  # auto | faster_whisper | openai
    model: str = ""  # 留空用各 provider 默认模型
    language: str = "zh"
    initial_prompt: str = ""  # 默认空；CLI 可传偏置提示
    timeout_seconds: int = 600


@dataclass(frozen=True)
class ASRResult:
    text: str
    segments: tuple[tuple[float, float, str], ...]
    provider: str
    model: str


def format_timestamp(seconds: float) -> str:
    """秒 → SRT 时间戳 HH:MM:SS,mmm（沿用参考实现）。"""
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _run_ffmpeg(command: list[str], runner: Callable, *, what: str) -> None:
    """静默跑 ffmpeg 提音命令；失败按 returncode 抛中文 ASRError。"""
    completed = runner(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    returncode = getattr(completed, "returncode", 0)
    if returncode != 0:
        raise ASRError(f"ffmpeg {what}失败（返回码 {returncode}）。请确认 ffmpeg 已装且输入文件可读。")


def _extract_wav_command(media_path: Path, wav_path: Path) -> list[str]:
    return [
        "ffmpeg", "-i", str(media_path),
        "-vn", "-ac", "1", "-ar", "16000",
        "-f", "wav", "-y", str(wav_path),
    ]


def _extract_mp3_command(media_path: Path, mp3_path: Path) -> list[str]:
    return [
        "ffmpeg", "-i", str(media_path),
        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
        "-f", "mp3", "-y", str(mp3_path),
    ]


def _extract_mp3_chunk_command(media_path: Path, mp3_path: Path, start: int, length: int) -> list[str]:
    return [
        "ffmpeg", "-ss", str(start), "-t", str(length), "-i", str(media_path),
        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
        "-f", "mp3", "-y", str(mp3_path),
    ]


def _probe_duration_seconds(media_path: Path, runner: Callable) -> float:
    command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(media_path),
    ]
    completed = runner(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if getattr(completed, "returncode", 0) != 0:
        raise ASRError("ffprobe 读取媒体时长失败。请确认 ffprobe 已装且输入文件可读。")
    try:
        payload = json.loads(getattr(completed, "stdout", "") or "{}")
        return float(payload.get("format", {}).get("duration") or 0.0)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ASRError("ffprobe 返回的时长信息无法解析。") from exc


def _transcribe_faster_whisper(media_path: Path, config: ASRConfig, runner: Callable) -> ASRResult:
    model_name = config.model or FASTER_WHISPER_DEFAULT_MODEL
    try:
        import faster_whisper  # 惰性导入：未装时给安装提示，绝不在模块顶层 import
    except ImportError as exc:
        raise ASRError(
            'faster-whisper 未安装。请运行 pip install -e ".[asr]" 后重试，'
            "或改用 openai provider（设置 OPENAI_API_KEY）。"
        ) from exc

    with tempfile.TemporaryDirectory() as tmp:
        wav_path = Path(tmp) / "audio.wav"
        _run_ffmpeg(_extract_wav_command(media_path, wav_path), runner, what="提取 16kHz 单声道音频")
        # 模型加载/推理可能抛任意异常（模型名错误、缓存损坏、首次下载失败、
        # ctranslate2 不支持当前 CPU 等），统一归一化为 ASRError，保证 CLI 报错友好。
        try:
            model = faster_whisper.WhisperModel(model_name, device="cpu", compute_type="int8")
            segments, _info = model.transcribe(
                str(wav_path),
                language=config.language,
                initial_prompt=config.initial_prompt or None,
                vad_filter=True,
                beam_size=5,
            )
            collected: list[tuple[float, float, str]] = []
            for segment in segments:
                text = str(segment.text).strip()
                if text:
                    collected.append((float(segment.start), float(segment.end), text))
        except ASRError:
            raise
        except Exception as exc:
            raise ASRError(
                f"faster-whisper 转写失败（模型 {model_name}）：{exc}。"
                "可尝试更换 --model 或检查模型缓存与网络。"
            ) from exc

    full_text = "\n".join(text for _s, _e, text in collected).strip()
    return ASRResult(full_text, tuple(collected), "faster_whisper", model_name)


def _require_openai_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ASRError("缺少 OPENAI_API_KEY 环境变量。请先设置 API Key，再运行云端语音转写。")
    return api_key


def _encode_multipart(audio_bytes: bytes, filename: str, model: str) -> tuple[bytes, str]:
    """手工构造 multipart/form-data：file + model 两个字段。"""
    # 防御：filename 进 Content-Disposition 头，剥掉引号与 CR/LF 防头注入
    # （当前调用方都是内部固定名，此处仅为未来外部文件名兜底）。
    filename = filename.replace('"', "").replace("\r", "").replace("\n", "")
    boundary = f"----videofactory{uuid.uuid4().hex}"
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    suffix = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = prefix + audio_bytes + suffix
    return body, boundary


def _openai_transcribe_bytes(audio_bytes: bytes, filename: str, config: ASRConfig, api_key: str) -> str:
    model = config.model or OPENAI_DEFAULT_MODEL
    body, boundary = _encode_multipart(audio_bytes, filename, model)
    request = Request(
        OPENAI_TRANSCRIBE_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=config.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except OSError:
            pass
        raise ASRError(f"OpenAI 转写 HTTP {exc.code}：{detail or exc.reason}") from exc
    except URLError as exc:
        raise ASRError(f"OpenAI 转写连接失败：{exc.reason}。请检查网络或代理设置。") from exc
    except json.JSONDecodeError as exc:
        raise ASRError("OpenAI 转写返回了非 JSON 响应。") from exc

    if not isinstance(payload, dict):
        raise ASRError(f"OpenAI 转写返回了非预期的 JSON 结构：{str(payload)[:200]}")
    return str(payload.get("text") or "").strip()


def _transcribe_openai(media_path: Path, config: ASRConfig, runner: Callable) -> ASRResult:
    api_key = _require_openai_key()
    model = config.model or OPENAI_DEFAULT_MODEL
    duration = _probe_duration_seconds(media_path, runner)
    with tempfile.TemporaryDirectory() as tmp:
        # 预估体积明显超限就直接分块，跳过整段提取（留 10% 余量防估算偏差）。
        if duration * MP3_BYTES_PER_SECOND > OPENAI_MAX_UPLOAD_BYTES * 0.9:
            text = _transcribe_openai_chunked(media_path, config, api_key, runner, Path(tmp), duration)
        else:
            mp3_path = Path(tmp) / "audio.mp3"
            _run_ffmpeg(_extract_mp3_command(media_path, mp3_path), runner, what="提取 16kHz 单声道音频")
            if mp3_path.stat().st_size <= OPENAI_MAX_UPLOAD_BYTES:
                text = _openai_transcribe_bytes(mp3_path.read_bytes(), "audio.mp3", config, api_key)
            else:
                # 估算失手（如高信息量音频），回退分块，复用已探测的时长。
                text = _transcribe_openai_chunked(media_path, config, api_key, runner, Path(tmp), duration)
    return ASRResult(text.strip(), (), "openai", model)


def _transcribe_openai_chunked(
    media_path: Path,
    config: ASRConfig,
    api_key: str,
    runner: Callable,
    tmp_dir: Path,
    duration: float | None = None,
) -> str:
    """超体积上限时按固定秒数分块提取、逐块转写再拼接。"""
    if duration is None:
        duration = _probe_duration_seconds(media_path, runner)
    parts: list[str] = []
    start = 0
    index = 0
    while start < duration:
        chunk_path = tmp_dir / f"chunk_{index:03d}.mp3"
        _run_ffmpeg(
            _extract_mp3_chunk_command(media_path, chunk_path, start, OPENAI_CHUNK_SECONDS),
            runner,
            what=f"提取第 {index + 1} 段分块音频",
        )
        part = _openai_transcribe_bytes(chunk_path.read_bytes(), chunk_path.name, config, api_key)
        if part:
            parts.append(part)
        start += OPENAI_CHUNK_SECONDS
        index += 1
    return "\n".join(parts)


def _resolve_auto_provider() -> str:
    try:
        import faster_whisper  # noqa: F401
        return "faster_whisper"
    except ImportError:
        pass
    if os.getenv("OPENAI_API_KEY", "").strip():
        return "openai"
    raise ASRError(
        "auto 模式找不到可用的转写后端。二选一：\n"
        '1. 安装本地模型：pip install -e ".[asr]"（faster-whisper，离线可用）；\n'
        "2. 使用云端：设置 OPENAI_API_KEY 环境变量后 --provider openai。"
    )


def transcribe_media(
    media_path: Path | str,
    config: ASRConfig | None = None,
    runner: Callable = subprocess.run,
) -> ASRResult:
    """把视频/音频文件转写成文本。失败时抛 ASRError。"""
    media_path = Path(media_path)
    if not media_path.exists():
        raise ASRError(f"输入媒体文件不存在：{media_path}")
    config = config or ASRConfig()
    provider = (config.provider or "auto").strip().lower()
    if provider == "auto":
        provider = _resolve_auto_provider()

    if provider == "faster_whisper":
        return _transcribe_faster_whisper(media_path, config, runner)
    if provider == "openai":
        return _transcribe_openai(media_path, config, runner)
    raise ASRError(f"不支持的 ASR provider：{provider}（可选 auto / faster_whisper / openai）")


def write_transcript_outputs(result: ASRResult, output_dir: Path | str) -> dict[str, Path]:
    """写 transcript.txt（始终）+ transcript.srt（有分段时）。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Path] = {}

    txt_path = output_dir / "transcript.txt"
    txt_path.write_text(result.text + "\n", encoding="utf-8")
    outputs["transcript_txt"] = txt_path

    if result.segments:
        srt_path = output_dir / "transcript.srt"
        srt_path.write_text(_build_srt(result.segments), encoding="utf-8")
        outputs["transcript_srt"] = srt_path
    else:
        # 云端接口只回整段文本、无时间分段，只能写 txt。
        note_path = output_dir / "transcript_notes.txt"
        note_path.write_text(
            f"{result.provider} 未返回逐句时间分段，因此只生成 transcript.txt，未生成 SRT。\n",
            encoding="utf-8",
        )
        outputs["transcript_notes"] = note_path
    return outputs


def _build_srt(segments: tuple[tuple[float, float, str], ...]) -> str:
    blocks = []
    for index, (start, end, text) in enumerate(segments, start=1):
        blocks.append(
            f"{index}\n{format_timestamp(start)} --> {format_timestamp(end)}\n{text}\n"
        )
    return "\n".join(blocks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m video_factory.asr",
        description="视频/音频 → 语音转写（输出 transcript.txt + transcript.srt）",
    )
    parser.add_argument("--input", required=True, help="输入视频或音频文件")
    parser.add_argument("--provider", default="auto", choices=["auto", "faster_whisper", "openai"])
    parser.add_argument(
        "--model",
        default="",
        help=f"留空则 faster_whisper 用 {FASTER_WHISPER_DEFAULT_MODEL}、openai 用 {OPENAI_DEFAULT_MODEL}",
    )
    parser.add_argument("--language", default="zh", help="识别语言（默认 zh）")
    parser.add_argument("--prompt", default="", help="偏置提示词：术语、人名等")
    parser.add_argument("--output", default="video_factory/output/transcript", help="输出目录")
    args = parser.parse_args(argv)

    try:
        config = ASRConfig(
            provider=args.provider,
            model=args.model,
            language=args.language,
            initial_prompt=args.prompt,
        )
        result = transcribe_media(args.input, config)
        outputs = write_transcript_outputs(result, args.output)
    except (ASRError, OSError) as exc:
        print(f"转写失败：{exc}")
        return 1

    print(f"转写完成：{len(result.text)} 字（provider={result.provider} model={result.model}）")
    for label, path in outputs.items():
        print(f"- {label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
