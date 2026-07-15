import json

import pytest

from video_factory.llm import LLMConfig, LLMProviderError, chat_completion


class FakeResponse:
    def __init__(self, body: dict):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self._body).encode("utf-8")


def _capture_urlopen(monkeypatch, body: dict):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(body)

    monkeypatch.setattr("video_factory.llm.urlopen", fake_urlopen)
    return captured


def test_openai_chat_completion_returns_text_and_sends_bearer(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    captured = _capture_urlopen(
        monkeypatch, {"choices": [{"message": {"content": "改写后的文案"}}]}
    )

    reply = chat_completion("system", "user", LLMConfig(provider="openai"))

    assert reply == "改写后的文案"
    request = captured["request"]
    assert request.full_url == "https://api.openai.com/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer sk-test-123"
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["model"] == "gpt-4o"
    assert payload["messages"][0] == {"role": "system", "content": "system"}


def test_anthropic_chat_completion_returns_text_and_sends_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-test-456")
    captured = _capture_urlopen(
        monkeypatch,
        {"content": [{"type": "text", "text": "第一段"}, {"type": "text", "text": "第二段"}]},
    )

    reply = chat_completion("system", "user", LLMConfig(provider="anthropic"))

    assert reply == "第一段第二段"
    request = captured["request"]
    assert request.full_url == "https://api.anthropic.com/v1/messages"
    assert request.get_header("X-api-key") == "ak-test-456"
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["system"] == "system"
    assert payload["model"] == "claude-sonnet-5"


def test_custom_endpoint_and_model_override(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    captured = _capture_urlopen(monkeypatch, {"choices": [{"message": {"content": "ok"}}]})

    config = LLMConfig(provider="openai", model="gpt-4o-mini", endpoint="https://gateway.local/v1/chat/completions")
    chat_completion("s", "u", config)

    request = captured["request"]
    assert request.full_url == "https://gateway.local/v1/chat/completions"
    assert json.loads(request.data.decode("utf-8"))["model"] == "gpt-4o-mini"


def test_missing_api_key_raises_actionable_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(LLMProviderError, match="OPENAI_API_KEY"):
        chat_completion("s", "u", LLMConfig(provider="openai"))


def test_unknown_provider_raises(monkeypatch):
    with pytest.raises(LLMProviderError, match="不支持的 LLM provider"):
        chat_completion("s", "u", LLMConfig(provider="gemini"))


def test_non_dict_json_body_raises_provider_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    _capture_urlopen(monkeypatch, [1, 2, 3])

    with pytest.raises(LLMProviderError, match="非预期的 JSON 结构"):
        chat_completion("s", "u", LLMConfig(provider="openai"))


def test_empty_openai_content_raises(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    _capture_urlopen(monkeypatch, {"choices": [{"message": {"content": ""}}]})

    with pytest.raises(LLMProviderError, match="内容为空"):
        chat_completion("s", "u", LLMConfig(provider="openai"))


def test_deepseek_chat_completion_openai_compatible(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dsk-test-789")
    captured = _capture_urlopen(
        monkeypatch, {"choices": [{"message": {"content": "deepseek 回复"}}]}
    )

    reply = chat_completion("system", "user", LLMConfig(provider="deepseek"))

    assert reply == "deepseek 回复"
    request = captured["request"]
    assert request.full_url == "https://api.deepseek.com/chat/completions"
    assert request.get_header("Authorization") == "Bearer dsk-test-789"
    assert json.loads(request.data.decode("utf-8"))["model"] == "deepseek-chat"


def test_deepseek_missing_key_raises(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(LLMProviderError, match="DEEPSEEK_API_KEY"):
        chat_completion("s", "u", LLMConfig(provider="deepseek"))


def test_chat_completion_converts_incomplete_read_to_provider_error(monkeypatch):
    """响应体半途断连（IncompleteRead）必须归一为 LLMProviderError——
    各调用方的降级路径（翻译分块回退/选图回落/改写报错）都以它为契约。"""
    from http.client import IncompleteRead

    from video_factory import llm as llm_mod

    class _BrokenResponse:
        def read(self):
            raise IncompleteRead(b"partial", 1024)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(llm_mod, "urlopen", lambda req, timeout: _BrokenResponse())
    with pytest.raises(LLMProviderError, match="网络传输中断（已重试 1 次）"):
        chat_completion("s", "u", LLMConfig(provider="deepseek"))


def test_chat_completion_retries_once_on_transient_network_error(monkeypatch):
    """瞬时网络故障（连接被掐）重试一次成功 → 正常返回，不再一次毛刺杀整单
    （2026-07-15 真实事故：135s 任务死于单次 RemoteDisconnected）。"""
    import json as _json
    from http.client import RemoteDisconnected

    from video_factory import llm as llm_mod

    class _GoodResponse:
        def read(self):
            return _json.dumps(
                {"choices": [{"message": {"content": "改写结果"}}]}
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    calls = []

    def flaky_urlopen(req, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise RemoteDisconnected("Remote end closed connection without response")
        return _GoodResponse()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(llm_mod, "urlopen", flaky_urlopen)
    result = chat_completion("s", "u", LLMConfig(provider="deepseek"))
    assert result == "改写结果"
    assert len(calls) == 2  # 首次失败 + 重试成功
