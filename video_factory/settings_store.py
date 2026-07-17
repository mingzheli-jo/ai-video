"""非敏感设置的 YAML 持久化（settings.yaml，复用 credentials_store 的扁平解析/按行写回）。

与 credentials.yaml 分开存：这里是**可公开的偏好配置**（如生图风格提示词），
不含密钥；手改文件和网页保存两条路同源。值必须单行（扁平 YAML 子集）。
"""

from __future__ import annotations

from pathlib import Path

from video_factory.credentials_store import _parse_flat_yaml, save_credential as _save_line

# 项目根 settings.yaml（基于本文件定位，不随 CWD 变化）。
SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.yaml"

# 设置白名单：新增设置项时在这里登记（studio 的 /api/settings 只认白名单）。
SETTING_NAMES = (
    "IMAGE_STYLE_PROMPT",
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
    """读 settings.yaml 里的白名单设置（文件缺失返回空 dict）。"""
    target = Path(path) if path is not None else SETTINGS_PATH
    if not target.exists():
        return {}
    return _parse_flat_yaml(
        target.read_text(encoding="utf-8", errors="replace"), frozenset(SETTING_NAMES)
    )


def save_setting(name: str, value: str, path: Path | None = None) -> None:
    """按行更新/插入一个设置（保留注释与其余行）。换行折成空格——扁平 YAML 只支持单行。"""
    target = Path(path) if path is not None else SETTINGS_PATH
    if not target.exists():
        target.write_text(_HEADER, encoding="utf-8")
    _save_line(name, " ".join(str(value).split()), path=target)
