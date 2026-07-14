"""credentials_store 纯函数单测：扁平 YAML 解析 / 保存 / 加载。"""

import os

import pytest

from video_factory import credentials_store as cs

ALLOWED = frozenset({"DEEPSEEK_API_KEY", "OPENAI_API_KEY", "VOLC_TTS_APIKEY"})


def test_parse_flat_yaml_basic_and_quotes_and_comments():
    text = (
        "# 注释行\n"
        'DEEPSEEK_API_KEY: "dsk-1"\n'
        "OPENAI_API_KEY: sk-2\n"
        "\n"
        "VOLC_TTS_APIKEY: ''\n"          # 空值忽略
        "UNKNOWN_KEY: x\n"               # 白名单外忽略
    )
    result = cs._parse_flat_yaml(text, ALLOWED)
    assert result == {"DEEPSEEK_API_KEY": "dsk-1", "OPENAI_API_KEY": "sk-2"}


def test_parse_value_with_colon_kept():
    # 只按首个冒号切分：值里含 :// 不被破坏
    result = cs._parse_flat_yaml("OPENAI_API_KEY: https://gw/v1:8080\n", ALLOWED)
    assert result["OPENAI_API_KEY"] == "https://gw/v1:8080"


def test_save_credential_creates_file_with_template(tmp_path):
    path = tmp_path / "credentials.yaml"
    cs.save_credential("DEEPSEEK_API_KEY", "dsk-9", path=path)
    text = path.read_text(encoding="utf-8")
    assert 'DEEPSEEK_API_KEY: "dsk-9"' in text
    # 模板注释也在（用户能看懂）
    assert "豆包" in text or "写稿" in text


def test_save_credential_updates_in_place_preserving_comments(tmp_path):
    path = tmp_path / "credentials.yaml"
    path.write_text(
        "# 我的注释\nDEEPSEEK_API_KEY: \"old\"\nOPENAI_API_KEY: \"keep\"\n",
        encoding="utf-8",
    )
    cs.save_credential("DEEPSEEK_API_KEY", "new", path=path)
    text = path.read_text(encoding="utf-8")
    assert 'DEEPSEEK_API_KEY: "new"' in text
    assert "old" not in text
    assert "# 我的注释" in text          # 注释保留
    assert 'OPENAI_API_KEY: "keep"' in text  # 其它键不动
    # 没有重复行
    assert text.count("DEEPSEEK_API_KEY") == 1


def test_save_credential_appends_when_key_absent(tmp_path):
    path = tmp_path / "credentials.yaml"
    path.write_text("OPENAI_API_KEY: \"x\"\n", encoding="utf-8")
    cs.save_credential("VOLC_TTS_APIKEY", "vk", path=path)
    text = path.read_text(encoding="utf-8")
    assert 'VOLC_TTS_APIKEY: "vk"' in text
    assert 'OPENAI_API_KEY: "x"' in text


def test_load_into_env_fills_empty_but_respects_existing(tmp_path, monkeypatch):
    path = tmp_path / "credentials.yaml"
    path.write_text(
        'DEEPSEEK_API_KEY: "from-file"\nOPENAI_API_KEY: "file-openai"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "already-set")  # 真实环境变量优先

    loaded = cs.load_into_env(ALLOWED, path=path)

    assert "DEEPSEEK_API_KEY" in loaded
    assert os.environ["DEEPSEEK_API_KEY"] == "from-file"
    assert "OPENAI_API_KEY" not in loaded  # 已有值不覆盖
    assert os.environ["OPENAI_API_KEY"] == "already-set"


def test_load_into_env_missing_file_returns_empty(tmp_path):
    assert cs.load_into_env(ALLOWED, path=tmp_path / "nope.yaml") == []


def test_save_then_load_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "credentials.yaml"
    cs.save_credential("VOLC_TTS_APIKEY", "vk-round", path=path)
    monkeypatch.delenv("VOLC_TTS_APIKEY", raising=False)
    cs.load_into_env(ALLOWED, path=path)
    assert os.environ["VOLC_TTS_APIKEY"] == "vk-round"
