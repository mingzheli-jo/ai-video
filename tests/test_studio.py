"""studio.py 单测（P10，全部离线）。

用 threading 起真服务于 127.0.0.1 随机端口 + http.client 打请求；monkeypatch
batch.STAGE_RUNNERS 使任务瞬时完成/失败，绝不真跑任何阶段模块。

覆盖：/api/meta 字段完整（含 credentials/dependencies 布尔）；credentials POST
白名单校验、设置后 meta 变 true 且响应不含 value；upload 文件名穿越/后缀黑名单/正常
落盘；/api/jobs 缺 source 返回 400 中文；合法 job 走 fake runner 到 ok 且 stages_done
顺序正确、final 指向预期文件名；失败 runner → failed + stage_failed + 中文 error；
/media 越界 403、界内 200、mp4 Range 206；batch/validate 逐条错误。
"""

import http.client
import json
import os
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from video_factory import batch, credentials_store, settings_store, studio


# ---------- 服务器脚手架 ----------

@pytest.fixture
def server(monkeypatch, tmp_path):
    """起真服务于随机端口；把产物根重定向到 tmp，STAGE_RUNNERS 换成 fake。"""
    output_root = tmp_path / "output"
    studio_root = output_root / "studio"
    monkeypatch.setattr(studio, "OUTPUT_ROOT", output_root)
    monkeypatch.setattr(studio, "STUDIO_ROOT", studio_root)
    monkeypatch.setattr(studio, "UPLOAD_ROOT", studio_root / "uploads")
    monkeypatch.setattr(studio, "JOBS_ROOT", studio_root / "jobs")
    # 凭据/设置 YAML 都重定向到 tmp，绝不写到项目真实文件
    monkeypatch.setattr(credentials_store, "CREDENTIALS_PATH", tmp_path / "credentials.yaml")
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.yaml")
    for _env in ("IMAGE_STYLE_PROMPT", "REWRITE_STYLE_PROMPT", "SUBTITLE_FONT_SIZE", "SUBTITLE_FONT_NAME"):
        monkeypatch.delenv(_env, raising=False)
    for d in (studio_root, studio_root / "uploads", studio_root / "jobs"):
        d.mkdir(parents=True, exist_ok=True)

    store = studio.TaskStore()
    handler = studio.make_handler(store)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"port": port, "store": store, "tmp": tmp_path, "output_root": output_root}
    finally:
        httpd.shutdown()
        httpd.server_close()
        # /api/settings 处理器直接写 os.environ（绕过 monkeypatch），而 monkeypatch.delenv
        # 对「原本不存在」的 env 不注册 undo → 会泄漏到后续测试文件（例如污染 test_subtitles
        # 读到的 SUBTITLE_FONT_NAME）。这里显式清掉本套设置项 env，保证测试隔离。
        for _env in ("IMAGE_STYLE_PROMPT", "REWRITE_STYLE_PROMPT", "SUBTITLE_FONT_SIZE", "SUBTITLE_FONT_NAME"):
            os.environ.pop(_env, None)


def _request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    payload = None
    hdrs = dict(headers or {})
    if body is not None:
        payload = json.dumps(body).encode("utf-8") if isinstance(body, (dict, list)) else body
        hdrs.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=payload, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read()
    status = resp.status
    range_hdr = resp.getheader("Content-Range")
    conn.close()
    return status, data, range_hdr


def _json(port, method, path, body=None):
    status, data, _ = _request(port, method, path, body)
    return status, json.loads(data.decode("utf-8")) if data else {}


class _FakeRunners:
    """替换 STAGE_RUNNERS：可选让某阶段失败；成功阶段落一个占位产物文件。"""

    def __init__(self, fail_stage=None):
        self.fail_stage = fail_stage

    def _make(self, stage):
        def runner(job, job_dir):
            if stage == self.fail_stage:
                return 1
            _write_stage_output(stage, job_dir)
            return 0
        return runner

    def install(self, monkeypatch):
        runners = {stage: self._make(stage) for stage in ("rewrite", "assemble", "effects", "subtitles")}
        monkeypatch.setattr(batch, "STAGE_RUNNERS", runners)


def _write_stage_output(stage, job_dir):
    files = {
        "rewrite": "rewrite.json",
        "assemble": "release.mp4",
        "effects": "release_with_effects.mp4",
        "subtitles": "release_subtitled.mp4",
    }
    (job_dir / files[stage]).write_bytes(b"x" * 32)
    if stage == "assemble":
        (job_dir / "assembly_plan.json").write_text("{}", encoding="utf-8")


def _wait_status(store, task_id, target, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = store.get(task_id)
        if task and task["status"] == target:
            return task
        time.sleep(0.02)
    return store.get(task_id)


def _make_paths(tmp_path):
    source = tmp_path / "source.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:02,000\n你好\n", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    return str(source), str(assets)


# ---------- /api/meta ----------

def test_meta_has_all_fields(server):
    status, meta = _json(server["port"], "GET", "/api/meta")
    assert status == 200
    for key in ("platforms", "styles", "aspects", "fits", "tts_providers",
                "voice_defaults", "credentials", "dependencies"):
        assert key in meta
    assert set(meta["credentials"].keys()) == set(studio.CREDENTIAL_NAMES)
    assert all(isinstance(v, bool) for v in meta["credentials"].values())
    assert all(isinstance(v, bool) for v in meta["dependencies"].values())
    assert {"ffmpeg", "node", "faster_whisper", "edge_tts"} == set(meta["dependencies"].keys())
    assert len(meta["styles"]) == 7
    assert "douyin" in meta["platforms"]


# ---------- credentials ----------

def test_credentials_rejects_non_whitelisted(server):
    status, data = _json(server["port"], "POST", "/api/credentials", {"name": "EVIL", "value": "x"})
    assert status == 400
    assert "白名单" in data["error"]


def test_credentials_sets_and_never_echoes_value(server, monkeypatch):
    port = server["port"]
    monkeypatch.setenv("OPENAI_API_KEY", "")
    secret = "sk-super-secret-123"
    status, data = _json(port, "POST", "/api/credentials", {"name": "OPENAI_API_KEY", "value": secret})
    assert status == 200
    assert data["name"] == "OPENAI_API_KEY" and data["set"] is True
    assert secret not in json.dumps(data)  # 响应永不回显 value
    # meta 现在应显示 true
    _, meta = _json(port, "GET", "/api/meta")
    assert meta["credentials"]["OPENAI_API_KEY"] is True


def test_credentials_persist_to_yaml_and_reload(server, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    status, data = _json(server["port"], "POST", "/api/credentials",
                         {"name": "DEEPSEEK_API_KEY", "value": "dsk-persist-1"})
    assert status == 200 and data["persisted"] is True
    yaml_path = credentials_store.CREDENTIALS_PATH
    assert yaml_path.exists()
    # 明文落盘到 yaml（用户可手改），但键名可见、值不进任何 HTTP 响应
    assert "DEEPSEEK_API_KEY" in yaml_path.read_text(encoding="utf-8")
    # 模拟重启：清掉环境变量后从 yaml 重新加载
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    loaded = credentials_store.load_into_env(
        frozenset(studio.CREDENTIAL_NAMES), path=yaml_path)
    assert "DEEPSEEK_API_KEY" in loaded
    import os
    assert os.environ["DEEPSEEK_API_KEY"] == "dsk-persist-1"


# ---------- upload ----------

def test_upload_traversal_name_is_sanitized(server):
    port = server["port"]
    status, data = _json(port, "POST", "/api/upload?kind=source&name=../evil.mp4", body=b"data")
    assert status == 200
    # 落盘路径不能逃出 uploads/source
    saved = data["path"].replace("\\", "/")
    assert "/uploads/source/evil.mp4" in saved
    assert ".." not in saved.split("/uploads/")[1]


def test_upload_nested_path_stripped(server):
    port = server["port"]
    status, data = _json(port, "POST", "/api/upload?kind=source&name=a/b/c.mp4", body=b"data")
    assert status == 200
    assert data["path"].replace("\\", "/").endswith("/uploads/source/c.mp4")


def test_upload_rejects_bad_extension(server):
    status, data = _json(server["port"], "POST", "/api/upload?kind=source&name=evil.exe", body=b"data")
    assert status == 400
    assert "文件类型" in data["error"]


def test_upload_normal_lands_on_disk(server):
    port = server["port"]
    status, data = _json(port, "POST", "/api/upload?kind=source&name=clip.mp4", body=b"hello-bytes")
    assert status == 200
    from pathlib import Path
    saved = Path(data["path"])
    assert saved.exists()
    assert saved.read_bytes() == b"hello-bytes"


def test_upload_asset_group_dir(server):
    port = server["port"]
    status, data = _json(port, "POST", "/api/upload?kind=asset&group=g1&name=a.mp4", body=b"z")
    assert status == 200
    assert "/uploads/assets/g1/a.mp4" in data["path"].replace("\\", "/")


# ---------- jobs ----------

def test_job_missing_source_returns_400_chinese(server):
    status, data = _json(server["port"], "POST", "/api/jobs", {"assets": str(server["tmp"])})
    assert status == 400
    assert "errors" in data
    assert any("source" in e for e in data["errors"])


def test_valid_job_runs_to_ok_with_ordered_stages(server, monkeypatch):
    _FakeRunners().install(monkeypatch)
    port, store, tmp = server["port"], server["store"], server["tmp"]
    source, assets = _make_paths(tmp)
    status, data = _json(port, "POST", "/api/jobs", {
        "source": source, "assets": assets, "platform": "douyin", "style": "film_recap",
    })
    assert status == 200
    task = _wait_status(store, data["id"], "ok")
    assert task["status"] == "ok"
    # douyin 预设：subtitles=True effects=True → 四阶段全跑
    assert task["stages_done"] == ["rewrite", "assemble", "effects", "subtitles"]
    assert task["final"].replace("\\", "/").endswith("release_subtitled.mp4")


def test_job_effects_off_final_is_release(server, monkeypatch):
    _FakeRunners().install(monkeypatch)
    port, store, tmp = server["port"], server["store"], server["tmp"]
    source, assets = _make_paths(tmp)
    status, data = _json(port, "POST", "/api/jobs", {
        "source": source, "assets": assets, "effects": False, "subtitles": False,
    })
    assert status == 200
    task = _wait_status(store, data["id"], "ok")
    assert task["stages_done"] == ["rewrite", "assemble"]
    assert task["final"].replace("\\", "/").endswith("release.mp4")


def test_failed_runner_records_stage_and_chinese_error(server, monkeypatch):
    _FakeRunners(fail_stage="assemble").install(monkeypatch)
    port, store, tmp = server["port"], server["store"], server["tmp"]
    source, assets = _make_paths(tmp)
    status, data = _json(port, "POST", "/api/jobs", {"source": source, "assets": assets})
    assert status == 200
    task = _wait_status(store, data["id"], "failed")
    assert task["status"] == "failed"
    assert task["stage_failed"] == "assemble"
    assert "阶段" in task["error"]


def test_jobs_list_newest_first(server, monkeypatch):
    _FakeRunners().install(monkeypatch)
    port, tmp = server["port"], server["tmp"]
    source, assets = _make_paths(tmp)
    _json(port, "POST", "/api/jobs", {"source": source, "assets": assets, "name": "first"})
    _json(port, "POST", "/api/jobs", {"source": source, "assets": assets, "name": "second"})
    _, data = _json(port, "GET", "/api/jobs")
    names = [j["name"] for j in data["jobs"]]
    assert names.index("second") < names.index("first")


# ---------- /media ----------

def test_media_out_of_tree_forbidden(server):
    port = server["port"]
    status, data = _json(port, "GET", "/media?path=" + str(server["tmp"] / "secret.txt"))
    assert status == 403
    assert "禁止" in data["error"] or "output" in data["error"]


def test_media_in_tree_ok(server):
    port, output_root = server["port"], server["output_root"]
    target = output_root / "studio" / "jobs" / "note.txt"
    target.write_text("hello media", encoding="utf-8")
    from urllib.parse import quote
    status, data, _ = _request(port, "GET", "/media?path=" + quote(str(target)))
    assert status == 200
    assert data == b"hello media"


def test_media_mp4_range_returns_206(server):
    port, output_root = server["port"], server["output_root"]
    target = output_root / "studio" / "jobs" / "clip.mp4"
    payload = bytes(range(256)) * 8  # 2048 bytes
    target.write_bytes(payload)
    from urllib.parse import quote
    status, data, range_hdr = _request(
        port, "GET", "/media?path=" + quote(str(target)), headers={"Range": "bytes=0-1023"}
    )
    assert status == 206
    assert data == payload[:1024]
    assert range_hdr == f"bytes 0-1023/{len(payload)}"


# ---------- batch/validate ----------

def test_batch_validate_reports_per_job_errors(server):
    port = server["port"]
    jobs = {"jobs": [
        {"name": "bad", "platform": "nonexist"},  # 缺 source/assets + platform 非法
        {"name": "also_bad"},
    ]}
    status, data = _json(port, "POST", "/api/batch/validate", jobs)
    assert status == 200
    results = data["results"]
    assert len(results) == 2
    assert all(not r["valid"] for r in results)
    assert any("platform" in "；".join(r["errors"]) for r in results)
    assert any("source" in "；".join(r["errors"]) for r in results)


def test_index_page_served(server):
    status, data, _ = _request(server["port"], "GET", "/")
    assert status == 200
    assert b"<!doctype html>" in data


# ---------- 审查回归：任务名穿越 / 内联 onclick 注入 / HEAD ----------

def test_job_name_traversal_sanitized_and_stays_within_output(server, monkeypatch):
    _FakeRunners().install(monkeypatch)
    source, assets = _make_paths(server["tmp"])
    status, data = _json(server["port"], "POST", "/api/jobs", {
        "name": "../../../evil", "source": source, "assets": assets,
        "effects": False, "subtitles": False,
    })
    assert status == 200
    assert data["name"] == "evil"  # 路径分隔符与 .. 被净化
    task = _wait_status(server["store"], data["id"], "ok")
    assert task["status"] == "ok"
    # 产物必须落在 output/ 树内：外层绝无 evil 目录，jobs/ 内有
    assert not (server["tmp"] / "evil").exists()
    assert (server["output_root"] / "studio" / "jobs" / "evil").exists()


def test_page_copy_button_uses_data_attribute_not_inline_onclick():
    from video_factory import studio_ui

    html = studio_ui.render_page()
    assert "data-copy=" in html
    # 内联 onclick 注入面已移除（HTML 实体在进 JS 前已解码，esc() 防不住该上下文）
    assert 'onclick="copyText(' not in html


def test_head_index_and_media(server):
    from urllib.parse import quote

    port = server["port"]
    status, data, _ = _request(port, "HEAD", "/")
    assert status == 200 and data == b""
    status, _, _ = _request(port, "HEAD", "/media?path=C:/Windows/win.ini")
    assert status == 403  # HEAD 与 GET 同守卫
    target = server["output_root"] / "studio" / "probe.mp4"
    target.write_bytes(b"x" * 64)
    status, data, _ = _request(port, "HEAD", f"/media?path={quote(str(target))}")
    assert status == 200 and data == b""  # 只发头不发体


def test_meta_llm_providers_and_deepseek_credential(server):
    status, meta = _json(server["port"], "GET", "/api/meta")
    assert status == 200
    assert "deepseek" in meta["llm_providers"]
    assert "auto" in meta["llm_providers"]
    assert "DEEPSEEK_API_KEY" in meta["credentials"]
    assert "VOLC_TTS_APIKEY" in meta["credentials"]  # 新版豆包控制台单 API Key


def test_credentials_accepts_deepseek_and_never_echoes(server, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")  # teardown 时恢复为空
    status, data = _json(server["port"], "POST", "/api/credentials",
                         {"name": "DEEPSEEK_API_KEY", "value": "dsk-secret"})
    assert status == 200
    assert "dsk-secret" not in json.dumps(data)
    _, meta = _json(server["port"], "GET", "/api/meta")
    assert meta["credentials"]["DEEPSEEK_API_KEY"] is True


# ---------- 生图风格设置 ----------

def test_settings_save_style_prompt_and_meta_reflects(server):
    port = server["port"]
    # meta 默认暴露内置美式漫画风
    status, meta = _json(port, "GET", "/api/meta")
    assert status == 200 and "美式漫画" in meta["image_style_prompt"]
    assert meta["image_style_prompt_default"] == meta["image_style_prompt"]

    # 保存自定义风格 → 立即生效 + 持久化 settings.yaml
    status, data = _json(port, "POST", "/api/settings",
                         {"name": "IMAGE_STYLE_PROMPT", "value": "水墨国风插画"})
    assert status == 200 and data["value"] == "水墨国风插画" and data["persisted"] is True
    assert settings_store.load_settings(server["tmp"] / "settings.yaml")["IMAGE_STYLE_PROMPT"] == "水墨国风插画"
    _, meta2 = _json(port, "GET", "/api/meta")
    assert meta2["image_style_prompt"] == "水墨国风插画"

    # 清空 = 恢复内置默认
    status, data = _json(port, "POST", "/api/settings", {"name": "IMAGE_STYLE_PROMPT", "value": ""})
    assert status == 200 and "美式漫画" in data["value"]


def test_settings_rejects_non_whitelisted_name(server):
    status, data = _json(server["port"], "POST", "/api/settings",
                         {"name": "EVIL_SETTING", "value": "x"})
    assert status == 400 and "白名单" in data["error"]


def test_page_contains_style_prompt_card():
    from video_factory import studio_ui

    html = studio_ui.render_page()
    assert 'id="stylePrompt"' in html and 'id="stylePromptSave"' in html


def test_page_has_transcript_compare_button_and_modal():
    """任务卡「文案对比」入口 + 模态框骨架都在页面里；按钮走事件委托 data 属性，无内联 onclick。"""
    from video_factory import studio_ui

    html = studio_ui.render_page()
    # 任务卡的对比按钮（事件委托）：脚本里以 data-compare 携带任务 id
    assert "data-compare=" in html
    # 模态框骨架（静态 HTML）：遮罩、左右两栏容器、关闭按钮都要在
    assert 'id="compareModal"' in html
    assert 'id="compareSource"' in html and 'id="compareRewrite"' in html
    assert 'id="compareClose"' in html
    assert "原视频文案" in html and "AI 改写稿" in html
    # 安全回归：对比按钮不得用内联 onclick（esc() 防不住 JS 字符串上下文）
    assert 'onclick="openCompare(' not in html


def test_page_has_voice_speed_input():
    """单条生产表单新增「配音语速」数字输入：范围 0.5~2.0、step 0.1、留空=默认。"""
    from video_factory import studio_ui

    html = studio_ui.render_page()
    assert 'id="f_voice_speed"' in html
    assert "配音语速" in html
    # 提交时 voice_speed 进 JSON（collectForm 里 put，留空不下发）
    assert "put('voice_speed'" in html


# ---------- CSRF / Origin 防护 ----------

def test_post_rejects_foreign_origin(server):
    """安全回归：跨站 Origin 的 POST 被 403 拒绝（防恶意网页 CSRF 打本地工作台）。"""
    status, _, _ = _request(
        server["port"], "POST", "/api/credentials",
        {"name": "OPENAI_API_KEY", "value": "x"},
        headers={"Origin": "http://evil.example"},
    )
    assert status == 403


def test_post_allows_same_origin(server):
    """同源 Origin（本机）的 POST 正常放行。"""
    port = server["port"]
    status, _, _ = _request(
        port, "POST", "/api/batch/validate", {"jobs": [{"name": "j"}]},
        headers={"Origin": f"http://127.0.0.1:{port}"},
    )
    assert status == 200


def test_post_without_origin_allowed(server):
    """无 Origin 头（curl/本地脚本）放行，不破坏本地工具既有用法。"""
    status, _, _ = _request(
        server["port"], "POST", "/api/batch/validate", {"jobs": [{"name": "j"}]},
    )
    assert status == 200


# ---------- 端口独占（防孪生进程） ----------

def test_single_instance_server_rejects_second_bind():
    """回归（2026-07-14 事故）：SO_REUSEADDR 在 Windows 上允许两个工作台静默共享端口，
    请求随机落到其中一个（凭据存 A 进程、任务跑 B 进程）。第二个实例必须响亮失败。"""
    handler = studio.make_handler(studio.TaskStore())
    first = studio._SingleInstanceServer(("127.0.0.1", 0), handler)
    port = first.server_address[1]
    try:
        with pytest.raises(OSError):
            studio._SingleInstanceServer(("127.0.0.1", port), handler)
    finally:
        first.server_close()


# ---------- 改写文风指令设置（P14） ----------

def test_settings_save_rewrite_prompt_echoes_own_value(server):
    """回归：/api/settings 必须回显该设置项自己的生效值（历史 bug：硬编码回显生图提示词）。"""
    port = server["port"]
    status, data = _json(port, "POST", "/api/settings",
                         {"name": "REWRITE_STYLE_PROMPT", "value": "多用短句，别用家人们"})
    assert status == 200 and data["persisted"] is True
    assert data["value"] == "多用短句，别用家人们"        # 回显自己，不是生图提示词
    _, meta = _json(port, "GET", "/api/meta")
    assert meta["rewrite_style_prompt"] == "多用短句，别用家人们"
    # 清空 = 恢复内置模板（空串）
    status, data = _json(port, "POST", "/api/settings", {"name": "REWRITE_STYLE_PROMPT", "value": ""})
    assert status == 200 and data["value"] == ""


def test_page_has_rewrite_prompt_card():
    from video_factory import studio_ui

    html = studio_ui.render_page()
    assert 'id="rewritePrompt"' in html and 'id="rewritePromptSave"' in html


# ---------- 字幕样式设置（字号系数 / 字体族） ----------

def test_meta_exposes_subtitle_font_defaults_and_options(server):
    _, meta = _json(server["port"], "GET", "/api/meta")
    assert meta["subtitle_font_size"] == 1.0                 # 默认字号系数
    assert meta["subtitle_font_name"] == "Microsoft YaHei"   # 默认字体族
    # 字体白名单暴露给前端下拉（五款中文字体，顺序固定）。
    assert meta["subtitle_font_options"] == [
        "Microsoft YaHei", "SimHei", "Source Han Sans SC", "KaiTi", "SimSun",
    ]


def test_settings_save_subtitle_font_size_roundtrip_and_meta(server):
    port = server["port"]
    # 保存 1.3 → 立即生效 + 持久化；回显自己的生效系数。
    status, data = _json(port, "POST", "/api/settings", {"name": "SUBTITLE_FONT_SIZE", "value": "1.3"})
    assert status == 200 and data["persisted"] is True and data["value"] == "1.3"
    assert settings_store.load_settings(server["tmp"] / "settings.yaml")["SUBTITLE_FONT_SIZE"] == "1.3"
    _, meta = _json(port, "GET", "/api/meta")
    assert meta["subtitle_font_size"] == 1.3
    # 越界值 5.0 → 钳位到 1.5 回显；清空 → 恢复默认 1.0。
    _, data = _json(port, "POST", "/api/settings", {"name": "SUBTITLE_FONT_SIZE", "value": "5.0"})
    assert data["value"] == "1.5"
    _, data = _json(port, "POST", "/api/settings", {"name": "SUBTITLE_FONT_SIZE", "value": ""})
    assert data["value"] == "1.0"


def test_settings_save_subtitle_font_name_roundtrip_and_meta(server):
    port = server["port"]
    status, data = _json(port, "POST", "/api/settings", {"name": "SUBTITLE_FONT_NAME", "value": "SimHei"})
    assert status == 200 and data["persisted"] is True and data["value"] == "SimHei"
    assert settings_store.load_settings(server["tmp"] / "settings.yaml")["SUBTITLE_FONT_NAME"] == "SimHei"
    _, meta = _json(port, "GET", "/api/meta")
    assert meta["subtitle_font_name"] == "SimHei"


def test_settings_subtitle_font_name_arbitrary_falls_back_on_read(server):
    """白名单拒绝任意字体：存进去的非白名单值在读取（meta/回显）时回落默认。"""
    port = server["port"]
    status, data = _json(port, "POST", "/api/settings", {"name": "SUBTITLE_FONT_NAME", "value": "Comic Sans"})
    assert status == 200
    assert data["value"] == "Microsoft YaHei"     # 回显已回落默认
    _, meta = _json(port, "GET", "/api/meta")
    assert meta["subtitle_font_name"] == "Microsoft YaHei"


def test_page_has_subtitle_style_card():
    from video_factory import studio_ui

    html = studio_ui.render_page()
    assert 'id="subFontSize"' in html
    assert 'id="subFontName"' in html
    assert 'id="subStyleSave"' in html
