"""全局测试隔离。

各阶段 CLI 的 main() 会自动加载项目根的 credentials.yaml（ensure_env_loaded），
不隔离的话：①测试进程会被灌入开发机的真实 API Key，污染"断言无凭据行为"的用例；
②任何调 save_credential 的测试 bug 都可能改写用户的真实凭据文件。
这里 autouse 把 CREDENTIALS_PATH 重定向到临时目录（不存在的文件 = 加载为空），
需要真实文件行为的测试自己往该路径写内容或显式传 path=。
"""

import pytest

from video_factory import credentials_store


@pytest.fixture(autouse=True)
def _isolate_credentials_yaml(tmp_path, monkeypatch):
    monkeypatch.setattr(
        credentials_store, "CREDENTIALS_PATH", tmp_path / "credentials.isolated.yaml"
    )
