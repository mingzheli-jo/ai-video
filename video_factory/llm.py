"""LLM chat provider layer (OpenAI / Anthropic).

与 pipeline.py 的 TTS provider 层同一风格：urllib 直调、零第三方依赖、
凭据只从环境变量读取、错误统一包装成带排查提示的异常。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OPENAI_DEFAULT_MODEL = "gpt-4o"
OPENAI_DEFAULT_URL = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-5"
ANTHROPIC_DEFAULT_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
# DeepSeek 是 OpenAI 兼容接口：同 chat/completions 报文与 Bearer 鉴权，仅换域名/模型/凭据。
DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"
DEEPSEEK_DEFAULT_URL = "https://api.deepseek.com/chat/completions"


class LLMProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "openai"  # openai | anthropic
    model: str = ""  # 留空用各 provider 默认模型
    api_key_env: str = ""  # 留空用各 provider 默认环境变量
    endpoint: str = ""  # 留空用官方地址；OpenAI 兼容网关可覆盖
    timeout_seconds: int = 180
    temperature: float = 0.7
    max_tokens: int = 4096


def _require_api_key(config: LLMConfig, default_env: str) -> str:
    env_name = config.api_key_env or default_env
    api_key = os.getenv(env_name, "").strip()
    if not api_key:
        raise LLMProviderError(
            f"缺少 {env_name} 环境变量。请先设置 API Key，再运行文案改写。"
        )
    return api_key


def _build_openai_style_request(
    system_prompt: str,
    user_prompt: str,
    config: LLMConfig,
    default_model: str,
    default_url: str,
    default_env: str,
) -> Request:
    """OpenAI 兼容报文（OpenAI/DeepSeek 共用）：chat/completions + Bearer 鉴权。"""
    api_key = _require_api_key(config, default_env)
    payload = {
        "model": config.model or default_model,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    return Request(
        config.endpoint or default_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )


def _build_openai_request(system_prompt: str, user_prompt: str, config: LLMConfig) -> Request:
    return _build_openai_style_request(
        system_prompt, user_prompt, config,
        OPENAI_DEFAULT_MODEL, OPENAI_DEFAULT_URL, "OPENAI_API_KEY",
    )


def _build_deepseek_request(system_prompt: str, user_prompt: str, config: LLMConfig) -> Request:
    return _build_openai_style_request(
        system_prompt, user_prompt, config,
        DEEPSEEK_DEFAULT_MODEL, DEEPSEEK_DEFAULT_URL, "DEEPSEEK_API_KEY",
    )


def _build_anthropic_request(system_prompt: str, user_prompt: str, config: LLMConfig) -> Request:
    api_key = _require_api_key(config, "ANTHROPIC_API_KEY")
    payload = {
        "model": config.model or ANTHROPIC_DEFAULT_MODEL,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    return Request(
        config.endpoint or ANTHROPIC_DEFAULT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        },
        method="POST",
    )


def _extract_openai_text(body: dict) -> str:
    choices = body.get("choices") or []
    if not choices:
        raise LLMProviderError(f"OpenAI 响应缺少 choices：{str(body)[:200]}")
    content = ((choices[0] or {}).get("message") or {}).get("content") or ""
    if not str(content).strip():
        raise LLMProviderError("OpenAI 响应内容为空。")
    return str(content)


def _extract_anthropic_text(body: dict) -> str:
    blocks = body.get("content") or []
    text = "".join(
        str(block.get("text") or "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    )
    if not text.strip():
        raise LLMProviderError(f"Anthropic 响应内容为空：{str(body)[:200]}")
    return text


def chat_completion(system_prompt: str, user_prompt: str, config: LLMConfig) -> str:
    """调用 LLM，返回纯文本回复。失败时抛 LLMProviderError。"""
    provider = (config.provider or "openai").strip().lower()
    if provider == "openai":
        request = _build_openai_request(system_prompt, user_prompt, config)
    elif provider == "deepseek":
        request = _build_deepseek_request(system_prompt, user_prompt, config)
    elif provider == "anthropic":
        request = _build_anthropic_request(system_prompt, user_prompt, config)
    else:
        raise LLMProviderError(
            f"不支持的 LLM provider：{provider}（可选 openai / anthropic / deepseek）"
        )

    try:
        with urlopen(request, timeout=config.timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except OSError:
            pass
        raise LLMProviderError(f"{provider} HTTP {exc.code}：{detail or exc.reason}") from exc
    except URLError as exc:
        raise LLMProviderError(f"{provider} 连接失败：{exc.reason}。请检查网络或代理设置。") from exc
    except json.JSONDecodeError as exc:
        raise LLMProviderError(f"{provider} 返回了非 JSON 响应。") from exc

    if not isinstance(body, dict):
        raise LLMProviderError(f"{provider} 返回了非预期的 JSON 结构：{str(body)[:200]}")

    if provider in ("openai", "deepseek"):  # deepseek 响应结构与 OpenAI 一致
        return _extract_openai_text(body)
    return _extract_anthropic_text(body)
