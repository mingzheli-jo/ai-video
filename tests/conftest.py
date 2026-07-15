"""全局测试隔离。

各阶段 CLI 的 main() 会自动加载项目根的 credentials.yaml（ensure_env_loaded），
不隔离的话：①测试进程会被灌入开发机的真实 API Key，污染"断言无凭据行为"的用例；
②任何调 save_credential 的测试 bug 都可能改写用户的真实凭据文件。
这里 autouse 把 CREDENTIALS_PATH 重定向到临时目录（不存在的文件 = 加载为空），
需要真实文件行为的测试自己往该路径写内容或显式传 path=。
"""

import pytest

from video_factory import credentials_store, settings_store


@pytest.fixture(autouse=True)
def _isolate_credentials_yaml(tmp_path, monkeypatch):
    monkeypatch.setattr(
        credentials_store, "CREDENTIALS_PATH", tmp_path / "credentials.isolated.yaml"
    )
    # settings.yaml 同样必须隔离：用户在工作台保存的真实偏好（字幕字号/字体等）
    # 曾把 4 个字幕排版测试打红（2026-07-15 实锤）。环境变量同名覆盖也一并清掉。
    monkeypatch.setattr(
        settings_store, "SETTINGS_PATH", tmp_path / "settings.isolated.yaml"
    )
    for env in settings_store.SETTING_NAMES:
        monkeypatch.delenv(env, raising=False)
