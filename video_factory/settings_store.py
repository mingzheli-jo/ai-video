"""非敏感设置的 YAML 持久化（settings.yaml，复用 credentials_store 的扁平解析/按行写回）。

与 credentials.yaml 分开存：这里是**可公开的偏好配置**（如生图风格提示词），
不含密钥；手改文件和网页保存两条路同源。值必须单行（扁平 YAML 子集）。
"""

from __future__ import annotations

import json
from pathlib import Path

# 项目根 settings.yaml（基于本文件定位，不随 CWD 变化）。
SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.yaml"

# 设置白名单：新增设置项时在这里登记（studio 的 /api/settings 只认白名单）。
SETTING_NAMES = (
    "IMAGE_STYLE_PROMPT",
    # 生图风格预设库（2026-07-18 用户定案：多套提示词、启用其一，切风格不丢词）。
    # 值为单行 JSON 数组：[{"name": "美式漫画", "prompt": "..."}, ...]
    "IMAGE_STYLE_PRESETS",
    # 生图模型 ID（2026-07-17：方舟按版本各给免费额度，可切版本吃额度）。
    "ARK_IMAGE_MODEL",
    "REWRITE_STYLE_PROMPT",
    "SUBTITLE_FONT_SIZE",
    # 竖/横屏独立字号系数（2026-07-16 用户定案）；缺失回落通用 SUBTITLE_FONT_SIZE。
    "SUBTITLE_FONT_SIZE_PORTRAIT",
    "SUBTITLE_FONT_SIZE_LANDSCAPE",
    "SUBTITLE_FONT_NAME",
)

_HEADER = (
    "# King-AI-video 偏好设置（不含密钥，可随意备份/分享）\n"
    "# 值必须写在一行内；改完保存本文件、重启服务即生效；\n"
    "# 网页「凭据与依赖」页保存时也会写回这里，两边同源。\n"
    "#\n"
    "# 生图风格提示词：会自动拼接到每条生图提示词之后，统一全片画风。\n"
    'IMAGE_STYLE_PROMPT: ""\n'
    "#\n"
    "# 改写文风指令：追加进 DeepSeek 改写的 system prompt，全局定制口吻/句式/禁忌词；\n"
    "# 留空 = 只用内置七种内容类型模板。\n"
    'REWRITE_STYLE_PROMPT: ""\n'
    "#\n"
    "# 字幕字号缩放系数：合法 0.7~1.5，留空 = 默认 1.0；乘在自适应基准字号上。\n"
    'SUBTITLE_FONT_SIZE: ""\n'
    "#\n"
    "# 字幕字体族：只认白名单（Microsoft YaHei / SimHei / Source Han Sans SC / KaiTi / SimSun），\n"
    "# 留空或非白名单 = 默认 Microsoft YaHei。\n"
    'SUBTITLE_FONT_NAME: ""\n'
)


def load_settings(path: Path | None = None) -> dict[str, str]:
    """读 settings.yaml 里的白名单设置（文件缺失返回空 dict）。

    2026-07-19 修复：不再复用凭据层的解析——那套会剥引号且配套的写入会**丢弃值内
    全部双引号**（对 API Key 是防注入，对含 JSON 的预设值是毁灭：引号剥光、重启后
    解析失败、预设蒸发，用户"每次重启都要重新设定"的根因）。
    新格式值为 JSON 字符串字面量（见 save_setting），旧格式裸值/成对引号兼容读取。
    """
    target = Path(path) if path is not None else SETTINGS_PATH
    if not target.exists():
        return {}
    allowed = frozenset(SETTING_NAMES)
    result: dict[str, str] = {}
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        if key not in allowed:
            continue
        value = value.strip()
        if value.startswith('"'):
            try:
                # 新格式：完整 JSON 字符串（内层引号 \" 、换行 \n 均无损还原）。
                # 旧格式 `KEY: "简单值"` 恰好也是合法 JSON 字符串，同路兼容。
                value = str(json.loads(value))
            except json.JSONDecodeError:
                # 旧坏行（曾被剥引号的残值）：按旧规则剥成对引号兜底。
                if len(value) >= 2 and value[0] == value[-1]:
                    value = value[1:-1]
        elif len(value) >= 2 and value[0] == value[-1] and value[0] == "'":
            value = value[1:-1]
        if value:
            result[key] = value
    return result


def save_setting(name: str, value: str, path: Path | None = None) -> None:
    """按行更新/插入一个设置（保留注释与其余行）。

    值以 JSON 字符串字面量落盘：内层双引号转义为 \\"、换行转义为 \\n——天然单行、
    无损往返、也无换行注入面（凭据层靠"丢字符"防注入，settings 靠转义防注入）。
    """
    target = Path(path) if path is not None else SETTINGS_PATH
    if not target.exists():
        target.write_text(_HEADER, encoding="utf-8")
    encoded = json.dumps(str(value), ensure_ascii=False)
    new_line = f"{name}: {encoded}"
    out: list[str] = []
    replaced = False
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not replaced and stripped and not stripped.startswith("#"):
            if stripped.partition(":")[0].strip() == name:
                out.append(new_line)
                replaced = True
                continue
        out.append(line)
    if not replaced:
        out.append(new_line)
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
