"""凭据的 YAML 持久化（零第三方依赖，仅支持扁平 KEY: value）。

设计：项目根 credentials.yaml 是唯一凭据来源。启动时读入 → 填进 os.environ；
网页「保存」时按行更新回同一文件（保留注释与手工编辑），因此手改 YAML 和网页保存
两条路同源。文件含明文密钥，已在 .gitignore 忽略，切勿提交或分享。

只实现扁平映射子集（每行 `KEY: value`，井号注释、空行）——凭据场景足够，
既是合法 YAML（将来装了 PyYAML 也能解析），又不引入依赖。
"""

from __future__ import annotations

import os
from pathlib import Path

# 项目根的 credentials.yaml（基于本文件定位，不随 CWD 变化）。
CREDENTIALS_PATH = Path(__file__).resolve().parent.parent / "credentials.yaml"

_HEADER = (
    "# King-AI-video 凭据配置（本文件含明文密钥，已在 .gitignore 忽略，切勿提交/分享）\n"
    "# 只填你要用的，其余留空即可。改完保存本文件、重启服务即生效；\n"
    "# 网页「凭据与依赖」页保存时也会写回这里，两边同源。\n"
    "#\n"
    "# 写稿 AI（三选一）：\n"
    'DEEPSEEK_API_KEY: ""\n'
    'OPENAI_API_KEY: ""\n'
    'ANTHROPIC_API_KEY: ""\n'
    "#\n"
    "# 豆包配音——新版控制台（快捷API接入）只填 APIKEY 一项；旧版填 APPID + TOKEN：\n"
    'VOLC_TTS_APIKEY: ""\n'
    'VOLC_TTS_APPID: ""\n'
    'VOLC_TTS_TOKEN: ""\n'
    "#\n"
    "# Pexels 素材下载（在 pexels.com/api 免费注册获取）：\n"
    'PEXELS_API_KEY: ""\n'
    "#\n"
    "# 豆包生图（火山方舟控制台的 API Key，与上面 TTS 的不是同一个）：\n"
    'ARK_API_KEY: ""\n'
)


def _parse_flat_yaml(text: str, allowed: frozenset[str]) -> dict[str, str]:
    """解析扁平 YAML：逐行 KEY: value，跳过空行/注释，剥两端成对引号，只留白名单键。"""
    result: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        if key not in allowed:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if value:
            result[key] = value
    return result


def load_into_env(allowed: frozenset[str], path: Path | None = None) -> list[str]:
    """把 YAML 里的凭据填进 os.environ（仅当该键当前为空——真实环境变量优先）。

    返回本次实际写入 os.environ 的键名列表。文件不存在则静默返回 []。
    """
    path = Path(path) if path is not None else CREDENTIALS_PATH
    if not path.exists():
        return []
    loaded: list[str] = []
    for key, value in _parse_flat_yaml(path.read_text(encoding="utf-8", errors="replace"), allowed).items():
        if not os.environ.get(key):  # 已有真实环境变量则不覆盖
            os.environ[key] = value
            loaded.append(key)
    return loaded


def _sanitize_credential_value(value: str) -> str:
    """净化凭据值，防注入。

    save_credential 把值拼进单行 `KEY: "value"`。若值里含换行，就能凭空造出额外的
    `OTHER_KEY: "..."` 行——下次启动被 load_into_env 当作合法凭据加载，从而**绕过调用方
    （studio/settings）对键名的白名单校验**、篡改任意其它凭据；含双引号则能提前闭合引号。
    这里逐字符重建：凡会被 str.splitlines() 当作换行的字符（\\n\\r\\v\\f\\x1c-\\x1e\\x85
    \\u2028\\u2029 全集，用 splitlines 判定而非硬编码那串不可见字符）一律丢弃，双引号也丢弃，
    再去两端空白。API Key/Token 本就是单行可见字符串，净化不会破坏任何合法值。
    """
    cleaned = "".join(
        ch for ch in str(value)
        if ch != '"' and len((ch + "x").splitlines()) == 1
    )
    return cleaned.strip()


def save_credential(name: str, value: str, path: Path | None = None) -> None:
    """按行更新/插入一个凭据到 YAML，保留其余行与注释。文件缺失则先写模板。"""
    path = Path(path) if path is not None else CREDENTIALS_PATH
    if not path.exists():
        path.write_text(_HEADER, encoding="utf-8")
    new_line = f'{name}: "{_sanitize_credential_value(value)}"'
    out: list[str] = []
    replaced = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not replaced and stripped and not stripped.startswith("#"):
            head = stripped.partition(":")[0].strip()
            if head == name:
                out.append(new_line)
                replaced = True
                continue
        out.append(line)
    if not replaced:
        out.append(new_line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:  # POSIX 上收紧权限（含密钥）；Windows 无此语义，忽略即可。
        os.chmod(path, 0o600)
    except OSError:
        pass
