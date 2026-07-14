"""credentials_store 纯函数单测：扁平 YAML 解析 / 保存 / 加载 / 值净化。"""

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


def test_save_credential_blocks_newline_injection(tmp_path):
    """安全回归：凭据值里的换行/引号不得注入出额外的白名单键行，绕过键名白名单。"""
    path = tmp_path / "credentials.yaml"
    # 攻击载荷：借写 DEEPSEEK 之机，试图凭空注入一行 OPENAI_API_KEY。
    payload = "x" + chr(34) + chr(10) + 'OPENAI_API_KEY: "sk-attacker'
    cs.save_credential("DEEPSEEK_API_KEY", payload, path=path)
    result = cs._parse_flat_yaml(path.read_text(encoding="utf-8"), ALLOWED)
    assert "OPENAI_API_KEY" not in result             # 绝不能凭空多出别的键
    assert chr(10) not in result["DEEPSEEK_API_KEY"]  # 换行已被剥离，值收在单行内


def test_sanitize_credential_value_strips_line_breaks_and_quotes():
    """净化只剥会被 splitlines() 当换行的字符（含 Unicode 行分隔符 U+2028/U+2029）与双引号，
    不动普通可见字符。用 chr() 构造输入以避开源码转义歧义。"""
    crlf = chr(13) + chr(10)  # \r\n
    assert cs._sanitize_credential_value("key" + crlf + "INJECT") == "keyINJECT"
    assert cs._sanitize_credential_value("a" + chr(0x2028) + "b") == "ab"   # Unicode 行分隔符也剥
    assert cs._sanitize_credential_value("a" + chr(34) + "b" + chr(34) + "c") == "abc"  # 双引号剥除
    assert cs._sanitize_credential_value("sk-live_AB.cd-12") == "sk-live_AB.cd-12"  # 合法 key 不动
