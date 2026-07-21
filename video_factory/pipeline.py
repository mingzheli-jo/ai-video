"""TTS 引擎：5 段流水线（改写 → 配音 → 出图 → 拼装 → 发布）的配音层。

只负责把口播文本合成为可用音轨，provider 覆盖 openai / doubao(v1+v3) / edge / file，
消费方是 assemble.py、voice.py、studio.py。本模块刻意不依赖 PIL、不依赖任何模板/
分镜结构——已退役的 v1 模板视频生成器已整体搬到 legacy_v1.py，依赖方向单向为
legacy_v1 → pipeline，反向依赖会把 PIL 重新拖回每一个 stage 的导入链。
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, replace
from http.client import HTTPException
from pathlib import Path
from typing import Callable, Dict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# TTS 网络重试预算（2026-07-21 实测 studio_0721_230653 上调）。配音失败是**致命**的
# ——不像生图能单拍降级，配音一挂整个任务当场判死（那次 267s 白跑），值得多试几次。
TTS_MAX_ATTEMPTS = 3
TTS_RETRY_BACKOFF_SECONDS = 0.5


def _urlopen_read_with_retry(request: Request, timeout: float, label: str) -> bytes:
    """发请求并读完响应体，瞬时网络故障自动重试。

    2026-07-21 实测：豆包 TTS 撞 RemoteDisconnected（"Remote end closed connection
    without response"），而这里零重试、异常网也漏——RemoteDisconnected 是 OSError
    子类，发生在 response.read() 阶段，URLError 兜不住，于是一路漏到 voice.py 的
    通用 OSError 分支被判死。

    异常网必须含 HTTPException/OSError：RemoteDisconnected 与 IncompleteRead 都在
    读响应体时抛出（2026-07-15 生图 IncompleteRead 击穿整单是同款教训）。
    HTTPError（明确状态码）不重试——鉴权/配额/参数问题重试无意义还多花钱。
    """
    last_error: Exception | None = None
    for attempt in range(TTS_MAX_ATTEMPTS):
        if attempt:
            time.sleep(TTS_RETRY_BACKOFF_SECONDS * attempt)   # 0.5s、1.0s 线性退避
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            raise TTSProviderError(f"{label} HTTP error: {exc.code}") from exc
        except (URLError, HTTPException, OSError) as exc:
            last_error = exc
    raise TTSProviderError(
        f"{label} connection error（已重试 {TTS_MAX_ATTEMPTS - 1} 次）：{last_error}"
    ) from last_error


@dataclass(frozen=True)
class TTSConfig:
    provider: str = "openai"
    model: str = "gpt-4o-mini-tts"
    voice: str = "marin"
    voice_instructions: str = "用中文短视频教程口吻，语速偏快但清楚，像经验型创作者在直接分享方法。"
    audio_file: Path | None = None
    allow_fallback: bool = False
    api_key_env: str = "OPENAI_API_KEY"
    timeout_seconds: int = 120
    edge_rate: str = "+20%"
    # 用户可调语速（1.0=原速；None=各 provider 默认）。统一钳到 0.5~2.0 后按各家
    # 契约换算：豆包 v1 speed_ratio 直传、v3 speech_rate=(r-1)*100、openai speed 直传、
    # edge 覆盖 edge_rate 为 ±N%。
    speed: float | None = None
    doubao_appid_env: str = "VOLC_TTS_APPID"
    doubao_token_env: str = "VOLC_TTS_TOKEN"
    doubao_cluster: str = "volcano_tts"
    # 新版豆包语音控制台（快捷API接入）只发一个 API Key；设了它就走 v3 接口，优先于 appid+token。
    doubao_apikey_env: str = "VOLC_TTS_APIKEY"


@dataclass(frozen=True)
class TTSResult:
    path: Path
    provider: str
    voice: str
    model: str
    used_fallback: bool
    notes: str


class TTSProviderError(Exception):
    """Raised when the configured TTS provider cannot produce release audio."""


# 语速安全区间：各家 TTS 的共同交集（豆包 v1 0.2~3、v3 ±50%~+100%、openai 0.25~4、
# edge 无硬限），超出后语音质量/自然度断崖式下滑，统一钳位。
_TTS_SPEED_MIN = 0.5
_TTS_SPEED_MAX = 2.0


def _tts_speed(tts_config: TTSConfig) -> float | None:
    """归一化用户语速：None/非法/非正 → None（用各 provider 默认），其余钳到安全区间。"""
    if tts_config.speed is None:
        return None
    try:
        value = float(tts_config.speed)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return max(_TTS_SPEED_MIN, min(_TTS_SPEED_MAX, value))


def _edge_rate_for(tts_config: TTSConfig) -> str:
    """edge-tts 的 --rate 参数：显式语速换算成 ±N%，未设时沿用 edge_rate 默认。"""
    speed = _tts_speed(tts_config)
    if speed is None:
        return tts_config.edge_rate
    pct = round((speed - 1) * 100)
    return f"{'+' if pct >= 0 else ''}{pct}%"


def build_openai_speech_payload(source, tts_config: TTSConfig) -> Dict[str, str]:
    payload = {
        "model": tts_config.model,
        "voice": tts_config.voice,
        "input": _resolve_narration(source),
        "instructions": tts_config.voice_instructions,
        "response_format": "wav",
    }
    speed = _tts_speed(tts_config)
    if speed is not None:
        payload["speed"] = speed
    return payload


DOUBAO_TTS_ENDPOINT = "https://openspeech.bytedance.com/api/v1/tts"
# v3 单向流式（HTTP chunked，每行一个 JSON 事件）：新版控制台 API Key 鉴权走这里。
DOUBAO_V3_TTS_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
# v3 资源 ID：语音合成1.0=seed-tts-1.0、2.0=seed-tts-2.0，可用环境变量覆盖。
DOUBAO_V3_RESOURCE_ID_ENV = "VOLC_TTS_RESOURCE_ID"
DOUBAO_V3_DEFAULT_RESOURCE_ID = "seed-tts-1.0"
DOUBAO_V3_URANUS_RESOURCE_ID = "seed-tts-2.0"
# v3 流式结束标志事件码（官方契约）。
DOUBAO_V3_DONE_CODE = 20000000

# TTSConfig.voice 的默认值 "marin" 是 openai 音色，对 edge/doubao 无意义（会被 API 拒）。
# 用户没显式指定音色（空或仍是 marin）时，各 provider 回落到自己的合理中文默认音色。
_CROSS_PROVIDER_DEFAULT_VOICE = "marin"
EDGE_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DOUBAO_DEFAULT_VOICE = "zh_male_liufei_uranus_bigtts"


def _resolve_provider_voice(voice: str, provider_default: str) -> str:
    if not voice or voice == _CROSS_PROVIDER_DEFAULT_VOICE:
        return provider_default
    return voice


def _doubao_v3_resource_for_voice(voice: str) -> str:
    """豆包 v3 资源 ID 必须与音色族匹配，错配会报 55000000（resource ID mismatched with
    speaker）。uranus 系音色（如刘飞 zh_male_liufei_uranus_bigtts）要 seed-tts-2.0，
    moon 系（爽快思思等）用 seed-tts-1.0。VOLC_TTS_RESOURCE_ID 显式设置时以它为准
    （给未来新音色族留逃生阀）。"""
    override = (os.getenv(DOUBAO_V3_RESOURCE_ID_ENV) or "").strip()
    if override:
        return override
    if "uranus" in (voice or "").lower():
        return DOUBAO_V3_URANUS_RESOURCE_ID
    return DOUBAO_V3_DEFAULT_RESOURCE_ID


def build_doubao_speech_payload(source, tts_config: TTSConfig, appid: str, token: str) -> Dict:
    return {
        "app": {"appid": appid, "token": token, "cluster": tts_config.doubao_cluster},
        "user": {"uid": "video_factory"},
        "audio": {
            "voice_type": tts_config.voice,
            "encoding": "mp3",
            "speed_ratio": _tts_speed(tts_config) or 1.0,
        },
        "request": {
            "reqid": str(uuid.uuid4()),
            "text": _resolve_narration(source),
            "operation": "query",
        },
    }


def build_doubao_v3_payload(source, tts_config: TTSConfig) -> Dict:
    """v3 单向流式请求体（官方契约：user + req_params.text/speaker/audio_params）。"""
    audio_params: Dict = {"format": "mp3", "sample_rate": 24000}
    speed = _tts_speed(tts_config)
    if speed is not None:
        # v3 语速契约：speech_rate ∈ [-50, 100]，0=原速、100=2倍、-50=0.5倍。
        audio_params["speech_rate"] = max(-50, min(100, round((speed - 1) * 100)))
    return {
        "user": {"uid": "video_factory"},
        "req_params": {
            "text": _resolve_narration(source),
            "speaker": tts_config.voice,
            "audio_params": audio_params,
        },
    }




def _resolve_narration(source) -> str:
    """兼容旧签名：既接受口播文本，也接受带 segments 的 VideoPlan（鸭子类型，
    避免 TTS 层反向依赖已退役的 legacy_v1.VideoPlan）。"""
    if isinstance(source, str):
        return source
    return " ".join(segment.narration for segment in source.segments)


def synthesize_voiceover_text(
    narration: str,
    voiceover_path: Path | str,
    tts_config: TTSConfig | None = None,
) -> TTSResult:
    """文本级 TTS 入口：只吃口播文本，供拼装流水线在没有 VideoPlan 时使用。

    与 synthesize_voiceover 共用同一套 provider 分发。tone 依赖 plan 段结构，
    这里没有 plan，因此禁用 fallback：openai/doubao/edge 缺凭证或失败时直接抛
    TTSProviderError，tone provider 本身不受支持。
    """
    if not str(narration or "").strip():
        raise TTSProviderError("口播文案为空，无法合成配音。")
    config = tts_config or TTSConfig()
    output = Path(voiceover_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    provider = config.provider.lower()

    def _no_fallback(reason: str) -> TTSResult:
        raise TTSProviderError(
            f"文本级 TTS 不支持 fallback 兜底音轨（{reason}）。请补全对应服务商凭证或改用 --audio。"
        )

    if provider == "openai":
        return _synthesize_openai_voiceover(narration, output, config, _no_fallback)
    if provider == "file":
        return _synthesize_file_voiceover(output, config)
    if provider == "edge":
        return _synthesize_edge_voiceover(narration, output, config, _no_fallback)
    if provider == "doubao":
        return _synthesize_doubao_voiceover(narration, output, config, _no_fallback)
    if provider == "tone":
        raise TTSProviderError("文本级 TTS 不支持 tone 节奏音轨（依赖分段结构）。请选择 openai/doubao/edge。")
    raise TTSProviderError(f"Unsupported TTS provider: {config.provider}")


def _synthesize_openai_voiceover(
    narration: str,
    output: Path,
    config: TTSConfig,
    on_fallback: Callable[[str], TTSResult],
) -> TTSResult:
    api_key = os.getenv(config.api_key_env)
    if not api_key:
        if config.allow_fallback:
            return on_fallback(f"{config.api_key_env} is missing; fallback tone track generated.")
        raise TTSProviderError(
            f"{config.api_key_env} is required for OpenAI TTS. "
            "Pass --allow-fallback for a non-release rhythm guide track."
        )

    payload = build_openai_speech_payload(narration, config)
    request = Request(
        "https://api.openai.com/v1/audio/speech",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "audio/wav",
        },
        method="POST",
    )

    output.write_bytes(
        _urlopen_read_with_retry(request, config.timeout_seconds, "OpenAI TTS")
    )

    if _probe_duration_seconds(output) <= 1:
        if config.allow_fallback:
            return on_fallback("OpenAI TTS returned unusable audio; fallback tone track generated.")
        raise TTSProviderError("OpenAI TTS returned unusable audio.")

    notes = "Voiceover generated with OpenAI Speech API.\n"
    _write_voiceover_notes(output, notes)
    return TTSResult(
        path=output,
        provider="openai",
        voice=config.voice,
        model=config.model,
        used_fallback=False,
        notes=notes.strip(),
    )


def _synthesize_doubao_voiceover(
    narration: str,
    output: Path,
    config: TTSConfig,
    on_fallback: Callable[[str], TTSResult],
) -> TTSResult:
    api_key = (os.getenv(config.doubao_apikey_env) or "").strip()
    appid = os.getenv(config.doubao_appid_env)
    token = os.getenv(config.doubao_token_env)
    if not api_key and (not appid or not token):
        if config.allow_fallback:
            return on_fallback(
                f"{config.doubao_apikey_env}/{config.doubao_appid_env} is missing; fallback tone track generated."
            )
        raise TTSProviderError(
            f"{config.doubao_apikey_env}（新版豆包语音控制台·快捷API接入的单个 API Key）"
            f"或 {config.doubao_appid_env}+{config.doubao_token_env}（旧版语音技术控制台）"
            "至少配置一种，才能使用 Doubao (Volcengine) TTS。"
            "Pass --allow-fallback for a non-release rhythm guide track."
        )

    config = replace(config, voice=_resolve_provider_voice(config.voice, DOUBAO_DEFAULT_VOICE))
    if api_key:
        mp3_bytes = _doubao_v3_fetch_audio(narration, config, api_key)
        model_label = "volcengine/v3-seed-tts"
    else:
        mp3_bytes = _doubao_v1_fetch_audio(narration, config, appid, token)
        model_label = f"volcengine/{config.doubao_cluster}"
    return _finalize_doubao_audio(mp3_bytes, output, config, on_fallback, model_label)


def _doubao_v1_fetch_audio(narration: str, config: TTSConfig, appid: str, token: str) -> bytes:
    """旧版 v1 接口（appid+token）：单次 JSON 响应，data 字段是整段 base64 音频。"""
    payload = build_doubao_speech_payload(narration, config, appid=appid, token=token)
    request = Request(
        DOUBAO_TTS_ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            # 火山引擎 v1 鉴权格式是 "Bearer;<token>"，分号不是笔误。
            "Authorization": f"Bearer;{token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    raw_body = _urlopen_read_with_retry(request, config.timeout_seconds, "Doubao TTS")
    try:
        body = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise TTSProviderError("Doubao TTS returned a non-JSON response.") from exc

    if body.get("code") != 3000 or not body.get("data"):
        raise TTSProviderError(
            f"Doubao TTS error {body.get('code')}: {body.get('message') or 'no audio data returned'}"
        )
    try:
        return base64.b64decode(body["data"])
    except (binascii.Error, ValueError) as exc:
        raise TTSProviderError("Doubao TTS returned malformed base64 audio.") from exc


def _doubao_v3_fetch_audio(narration: str, config: TTSConfig, api_key: str) -> bytes:
    """新版 v3 单向流式（API Key）：chunked 逐行 JSON 事件，data 是 base64 音频分片。"""
    payload = build_doubao_v3_payload(narration, config)
    resource_id = _doubao_v3_resource_for_voice(config.voice)
    request = Request(
        DOUBAO_V3_TTS_ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "X-Api-Key": api_key,
            "X-Api-Resource-Id": resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    raw = _urlopen_read_with_retry(
        request, config.timeout_seconds, "Doubao TTS(v3)"
    ).decode("utf-8", errors="replace")

    chunks: list[bytes] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TTSProviderError("Doubao TTS(v3) returned a non-JSON stream line.") from exc
        code = event.get("code")
        if code == DOUBAO_V3_DONE_CODE:
            break
        if code != 0:
            raise TTSProviderError(
                f"Doubao TTS(v3) error {code}: {event.get('message') or 'unknown'}"
            )
        data = event.get("data")
        if data:
            try:
                chunks.append(base64.b64decode(data))
            except (binascii.Error, ValueError) as exc:
                raise TTSProviderError("Doubao TTS(v3) returned malformed base64 audio.") from exc
    if not chunks:
        raise TTSProviderError("Doubao TTS(v3) returned no audio data.")
    return b"".join(chunks)


def _finalize_doubao_audio(
    mp3_bytes: bytes,
    output: Path,
    config: TTSConfig,
    on_fallback: Callable[[str], TTSResult],
    model_label: str,
) -> TTSResult:
    temp_media = output.with_suffix(".doubao.mp3")
    temp_media.write_bytes(mp3_bytes)
    if _probe_duration_seconds(temp_media) <= 1:
        temp_media.unlink(missing_ok=True)
        if config.allow_fallback:
            return on_fallback("Doubao TTS returned unusable audio; fallback tone track generated.")
        raise TTSProviderError("Doubao TTS returned unusable audio.")

    try:
        _convert_audio(temp_media, output)
    except subprocess.CalledProcessError as exc:
        raise TTSProviderError("Failed to convert Doubao TTS audio to WAV.") from exc
    finally:
        temp_media.unlink(missing_ok=True)
    if _probe_duration_seconds(output) <= 1:
        raise TTSProviderError("Converted Doubao TTS audio is not usable.")
    notes = f"Voiceover generated with Doubao (Volcengine) TTS voice {config.voice}.\n"
    _write_voiceover_notes(output, notes)
    return TTSResult(
        path=output,
        provider="doubao",
        voice=config.voice,
        model=model_label,
        used_fallback=False,
        notes=notes.strip(),
    )


def _synthesize_file_voiceover(output: Path, config: TTSConfig) -> TTSResult:
    if not config.audio_file:
        raise TTSProviderError("A local audio file is required for the file TTS provider.")
    source = Path(config.audio_file)
    if not source.exists():
        raise TTSProviderError(f"The configured audio file does not exist: {source}")
    _convert_audio(source, output)
    if _probe_duration_seconds(output) <= 1:
        raise TTSProviderError(f"The configured audio file is not usable: {source}")
    notes = f"Voiceover copied from local audio file: {source}\n"
    _write_voiceover_notes(output, notes)
    return TTSResult(
        path=output,
        provider="file",
        voice=str(source),
        model="local-file",
        used_fallback=False,
        notes=notes.strip(),
    )


def _synthesize_edge_voiceover(
    narration: str,
    output: Path,
    config: TTSConfig,
    on_fallback: Callable[[str], TTSResult],
) -> TTSResult:
    temp_media = output.with_suffix(".edge.mp3")
    voice = _resolve_provider_voice(config.voice, EDGE_DEFAULT_VOICE)
    command = [
        sys.executable,
        "-m",
        "edge_tts",
        "--voice",
        voice,
        "--rate",
        _edge_rate_for(config),
        "--text",
        narration,
        "--write-media",
        str(temp_media),
    ]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        temp_media.unlink(missing_ok=True)
        if config.allow_fallback:
            return on_fallback("edge-tts failed; fallback tone track generated.")
        raise TTSProviderError(
            "edge-tts failed. Install it with `python3 -m pip install edge-tts`, "
            "or pass --allow-fallback for a non-release rhythm guide track."
        ) from exc

    if _probe_duration_seconds(temp_media) <= 1:
        temp_media.unlink(missing_ok=True)
        if config.allow_fallback:
            return on_fallback("edge-tts returned unusable audio; fallback tone track generated.")
        raise TTSProviderError("edge-tts returned unusable audio.")

    try:
        _convert_audio(temp_media, output)
    except subprocess.CalledProcessError as exc:
        raise TTSProviderError("Failed to convert edge-tts audio to WAV.") from exc
    finally:
        temp_media.unlink(missing_ok=True)
    if _probe_duration_seconds(output) <= 1:
        raise TTSProviderError("Converted edge-tts audio is not usable.")
    notes = f"Voiceover generated with edge-tts voice {config.voice}.\n"
    _write_voiceover_notes(output, notes)
    return TTSResult(
        path=output,
        provider="edge",
        voice=config.voice,
        model="edge-tts",
        used_fallback=False,
        notes=notes.strip(),
    )


def _write_voiceover_notes(voiceover_path: Path, notes: str) -> None:
    voiceover_path.with_name("voiceover_notes.txt").write_text(notes, encoding="utf-8")


def _convert_audio(input_path: Path, output_path: Path) -> None:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        shutil.copyfile(input_path, output_path)
        return
    subprocess.run(
        [ffmpeg_path, "-y", "-i", str(input_path), "-ar", "44100", "-ac", "1", str(output_path)],
        check=True,
    )


def _probe_duration_seconds(path: Path) -> float:
    ffprobe_path = shutil.which("ffprobe")
    if not ffprobe_path or not path.exists():
        return 0.0
    try:
        result = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
    except subprocess.CalledProcessError:
        return 0.0
    payload = json.loads(result.stdout or "{}")
    try:
        return float(payload.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0
