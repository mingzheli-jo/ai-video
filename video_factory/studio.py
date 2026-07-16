"""创作工作台服务器（P10）。

面向不会用 CLI 的用户的本地 Web 控制台：把 rewrite→assemble→effects→subtitles
的批量生产链路图形化。任务执行 100% 复用 batch.py（resolve_job→validate_job→
run_job），本模块只负责 HTTP 服务、上传落盘、任务登记与产物回传。

设计约束：
- 纯标准库 http.server；只绑 127.0.0.1（本机个人工具）；上传走 raw-body POST（非
  multipart，不用已废弃的 cgi）。
- 凭据即时写进 os.environ 生效，并持久化到项目根 credentials.yaml（已 gitignore）；
  响应与日志绝不回显 value。
- /media 只服务 output/ 目录树内文件（防路径穿越），mp4 支持单段 Range 便于播放拖动。
- 任务表内存存储 + threading.Lock；进程重启任务清空（产物在盘上，可接受）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from video_factory import batch, credentials_store, settings_store, studio_ui
from video_factory.image_gen import DEFAULT_STYLE_PROMPT, get_style_prompt
from video_factory.rewrite import build_rewrite_prompts, get_rewrite_style_prompt
from video_factory.subtitles import (
    SUBTITLE_FONT_OPTIONS,
    get_subtitle_font_name,
    get_subtitle_font_scale,
)
from video_factory.assemble import ASPECT_PRESETS, FIT_MODES
from video_factory.batch import PLATFORM_PRESETS, resolve_job, validate_job
from video_factory.pipeline import DOUBAO_DEFAULT_VOICE, EDGE_DEFAULT_VOICE
from video_factory.rewrite_styles import STYLES

# ---------- 常量 ----------

OUTPUT_ROOT = Path("video_factory/output")
STUDIO_ROOT = OUTPUT_ROOT / "studio"
UPLOAD_ROOT = STUDIO_ROOT / "uploads"
JOBS_ROOT = STUDIO_ROOT / "jobs"
DEFAULT_PORT = 56090

# 凭据白名单（唯一权威名单在 credentials_store，与各阶段 CLI 的自动加载共用）。
CREDENTIAL_NAMES = credentials_store.ALL_CREDENTIAL_NAMES

TTS_PROVIDERS = ("doubao", "openai", "edge")
VOICE_DEFAULTS = {
    "doubao": DOUBAO_DEFAULT_VOICE,
    "edge": EDGE_DEFAULT_VOICE,
    "openai": "marin",
}

# 上传文件名安全化保留字符（字母数字中文与 ._-）之外一律剥除；后缀白名单。
_SAFE_EXTS = frozenset({
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".m4v",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg",
    ".srt", ".vtt", ".txt", ".json",
})
UPLOAD_KINDS = frozenset({"source", "bgm", "asset"})
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024  # 4GB
_UPLOAD_CHUNK = 1024 * 1024  # 1MB
# JSON 请求体上限（批量任务清单也远够）；防止谎报超大 Content-Length 撑爆内存。
MAX_JSON_BYTES = 8 * 1024 * 1024  # 8MB
# CSRF 防护：本工作台自身的合法来源主机。浏览器发起的跨站 POST 必带 Origin，据此拦截。
# 绝不能为消 CORS 报错而下发 Access-Control-Allow-Origin，那会让这道防线失效。
_ALLOWED_ORIGIN_HOSTS = frozenset({"127.0.0.1", "localhost"})


# ---------- 依赖 / 凭据探测 ----------

def _dependency_flags() -> dict[str, bool]:
    return {
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "node": shutil.which("node") is not None,
        "faster_whisper": importlib.util.find_spec("faster_whisper") is not None,
        "edge_tts": importlib.util.find_spec("edge_tts") is not None,
    }


def _credential_flags() -> dict[str, bool]:
    return {name: bool(os.environ.get(name)) for name in CREDENTIAL_NAMES}


def _persona_summary(persona: str) -> str:
    """风格人设摘要：取首句（截断），供 UI 卡片副文案兜底。"""
    text = persona.strip().split("。")[0]
    return (text[:40] + "…") if len(text) > 40 else text


_MUSIC_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")


def _list_music_files() -> list[dict]:
    """项目根 music/ 目录下的 BGM 候选（表单下拉用，2026-07-16 用户定案）。

    目录不存在/为空返回空列表；路径给绝对路径，assemble 直接可用。
    """
    music_dir = Path(__file__).resolve().parent.parent / "music"
    if not music_dir.is_dir():
        return []
    return [
        {"name": p.name, "path": str(p.resolve())}
        for p in sorted(music_dir.iterdir(), key=lambda x: x.name.lower())
        if p.is_file() and p.suffix.lower() in _MUSIC_EXTS
    ]


def build_meta() -> dict:
    """/api/meta：平台预设、风格、画幅/填充、TTS、默认音色、凭据与依赖布尔。"""
    platforms = {
        key: {
            "label": _PLATFORM_LABELS.get(key, key),
            "aspect": p.aspect,
            "fit": p.fit,
            "duration": p.duration,
            "subtitles": p.subtitles,
            "effects": p.effects,
        }
        for key, p in PLATFORM_PRESETS.items()
    }
    styles = [
        {
            "key": s.key,
            "label": s.label,
            "tts_hint": s.tts_hint,
            "persona": _persona_summary(s.persona),
            # 内置提示词透明化（2026-07-17 用户点名去黑盒）：下发该风格真实组装的
            # 完整 system prompt（含用户自定义全局指令），前端"提示词"按钮原文展示。
            "prompt": build_rewrite_prompts("", style=s.key)[0],
        }
        for s in STYLES.values()
    ]
    return {
        "platforms": platforms,
        "styles": styles,
        "aspects": list(ASPECT_PRESETS.keys()),
        "fits": list(FIT_MODES),
        "tts_providers": list(TTS_PROVIDERS),
        "llm_providers": ["auto", "openai", "anthropic", "deepseek"],
        "voice_defaults": dict(VOICE_DEFAULTS),
        "credentials": _credential_flags(),
        "dependencies": _dependency_flags(),
        # BGM 下拉候选：项目根 music/ 目录下的音频文件。
        "music_files": _list_music_files(),
        # 生图风格提示词：当前生效值 + 内置默认（UI 用于回显与"恢复默认"提示）。
        "image_style_prompt": get_style_prompt(),
        "image_style_prompt_default": DEFAULT_STYLE_PROMPT,
        # 改写文风指令（DeepSeek 提示词自定义，同款机制）：空=只用内置内容类型模板。
        "rewrite_style_prompt": get_rewrite_style_prompt(),
        # 字幕样式（用户可调）：当前生效字号系数、字体族，以及字体白名单（前端下拉用）。
        "subtitle_font_size": get_subtitle_font_scale(),
        # 竖/横屏独立字号（缺失时自动回落通用值，前端直接回填）。
        "subtitle_font_size_portrait": get_subtitle_font_scale(True),
        "subtitle_font_size_landscape": get_subtitle_font_scale(False),
        "subtitle_font_name": get_subtitle_font_name(),
        "subtitle_font_options": list(SUBTITLE_FONT_OPTIONS),
    }


_PLATFORM_LABELS = {
    "douyin": "抖音",
    "kuaishou": "快手",
    "shipinhao": "视频号",
    "xiaohongshu": "小红书",
    "bilibili": "B站",
}


# ---------- 任务存储 ----------

class TaskStore:
    """内存任务表（线程安全）。任务生命周期：queued→running→ok|failed|invalid。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, dict] = {}
        self._order: list[str] = []  # 新→旧（新任务插到列表头）

    def create(self, name: str, platform: str) -> dict:
        task_id = uuid.uuid4().hex[:12]
        task = {
            "id": task_id,
            "name": name,
            "platform": platform,
            "status": "queued",
            "current_stage": "",
            "stages_done": [],
            "stage_failed": None,
            "error": None,
            "final": "",
            "outputs": {},
            "elapsed_seconds": 0.0,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with self._lock:
            self._tasks[task_id] = task
            self._order.insert(0, task_id)
        return dict(task)

    def _mutate(self, task_id: str, **fields) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                task.update(fields)

    def mark_running(self, task_id: str) -> None:
        self._mutate(task_id, status="running")

    def mark_stage(self, task_id: str, stage: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            prev = task.get("current_stage")
            if prev and prev not in task["stages_done"]:
                task["stages_done"] = task["stages_done"] + [prev]
            task["current_stage"] = stage

    def finish(self, task_id: str, report) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            done = list(task["stages_done"])
            cur = task.get("current_stage")
            # 成功时把最后一个进行中的阶段也归入 done。
            if report.status == "ok" and cur and cur not in done:
                done.append(cur)
            payload = report.to_dict()
            task.update(
                status=report.status,
                current_stage="",
                stages_done=done,
                stage_failed=report.stage_failed,
                error=report.error,
                final=payload.get("final", ""),
                outputs=payload.get("outputs", {}),
                elapsed_seconds=payload.get("elapsed_seconds", 0.0),
            )

    def fail(self, task_id: str, error: str) -> None:
        self._mutate(task_id, status="failed", current_stage="", error=error)

    def get(self, task_id: str) -> dict | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task is not None else None

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(self._tasks[tid]) for tid in self._order if tid in self._tasks]


# ---------- 任务执行 ----------

def _run_task(store: TaskStore, task_id: str, job) -> None:
    """后台线程体：run_job 复用 batch 编排，双保险兜底任何逃逸异常写进任务 error。"""
    store.mark_running(task_id)
    try:
        report = batch.run_job(job, on_stage=lambda stage: store.mark_stage(task_id, stage))
        store.finish(task_id, report)
    except (Exception, SystemExit) as exc:  # run_job 已自兜底，这是双保险
        store.fail(task_id, f"任务执行异常：{exc}")


def _auto_job_name() -> str:
    return time.strftime("studio_%m%d_%H%M%S")


def _build_raw_job(form: dict) -> dict:
    """网页表单 dict → batch raw job dict。name 缺省自动生成；output 固定 studio/jobs/<name>。

    name 必须过 safe_filename 净化：它直接拼进输出路径（防 ../ 写穿越到
    output/ 之外），也会回显进页面（顺带消灭引号类注入字符）。
    """
    name = safe_filename(str(form.get("name") or "").strip()) or _auto_job_name()
    raw: dict = {"name": name, "output": str(JOBS_ROOT / name)}
    # 直接透传的字段（缺省则不放进 raw，交由 batch 走预设/默认回落）。
    passthrough = (
        "source", "assets", "platform", "style", "brief",
        "aspect", "fit", "tts", "voice", "bgm", "audio", "llm", "visual_source",
    )
    for key in passthrough:
        value = form.get(key)
        if value not in (None, ""):
            raw[key] = value
    for key in ("duration", "bgm_volume", "sfx_volume", "voice_speed"):
        value = form.get(key)
        if value not in (None, ""):
            raw[key] = value
    for key in ("subtitles", "effects", "lower_thirds", "sfx", "ambient_particles", "dual_aspect"):
        if key in form and form[key] is not None:
            raw[key] = _as_bool(form[key])
    return raw


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "on", "yes", "是")


def register_job(store: TaskStore, form: dict) -> tuple[int, dict]:
    """组 raw dict→resolve→validate；非法返回 400 errors，合法登记并起线程返回 {id,name}。"""
    raw = _build_raw_job(form)
    try:
        job = resolve_job(raw, 0)
    except (ValueError, TypeError) as exc:
        return HTTPStatus.BAD_REQUEST, {"errors": [f"任务字段无法解析：{exc}"]}
    errors = validate_job(job)
    if errors:
        return HTTPStatus.BAD_REQUEST, {"errors": errors}
    # 双重防线：即使 name 净化被未来改动削弱，产物目录也绝不允许落在 output/ 之外。
    if not _within_output(Path(job.output)):
        return HTTPStatus.BAD_REQUEST, {"errors": ["任务名非法：输出路径越界。"]}
    task = store.create(job.name, job.platform)
    thread = threading.Thread(target=_run_task, args=(store, task["id"], job), daemon=True)
    thread.start()
    return HTTPStatus.OK, {"id": task["id"], "name": job.name}


def validate_batch(raw_jobs: list[dict]) -> list[dict]:
    """批量校验：逐条返回 {name, platform, valid, errors}。"""
    results = []
    resolved = batch.resolve_all(raw_jobs)
    for job in resolved:
        errors = validate_job(job)
        results.append({
            "name": job.name,
            "platform": job.platform,
            "valid": not errors,
            "errors": errors,
        })
    return results


# ---------- 文件名安全化 ----------

def safe_filename(name: str) -> str:
    """剥路径分隔符，只留字母数字中文与 ._-；返回安全文件名（可能为空，调用方拒空）。"""
    base = name.replace("\\", "/").split("/")[-1].strip()
    kept = []
    for ch in base:
        if ch.isalnum() or ch in "._-" or "一" <= ch <= "鿿":
            kept.append(ch)
    cleaned = "".join(kept).strip("._")
    return cleaned


def _upload_dir(kind: str, group: str) -> Path:
    if kind == "source":
        return UPLOAD_ROOT / "source"
    if kind == "bgm":
        return UPLOAD_ROOT / "bgm"
    group_id = safe_filename(group) or uuid.uuid4().hex[:8]
    return UPLOAD_ROOT / "assets" / group_id


# ---------- HTTP 处理器 ----------

def _within_output(path: Path) -> bool:
    """路径穿越防护：resolve 后必须落在 output/ 目录树内。"""
    try:
        resolved = path.resolve()
        root = OUTPUT_ROOT.resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents


def _within_music(path: Path) -> bool:
    """BGM 试听白名单：项目根 music/ 目录树内（2026-07-16 选歌可播可拉进度）。"""
    try:
        resolved = path.resolve()
        root = (Path(__file__).resolve().parent.parent / "music").resolve()
    except OSError:
        return False
    return root in resolved.parents


def _media_allowed(path: Path) -> bool:
    """/media 可访问范围：output 产物树 + music BGM 库（试听）。"""
    return _within_output(path) or _within_music(path)


_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".srt": "text/plain; charset=utf-8",
    ".vtt": "text/plain; charset=utf-8",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
}


def make_handler(store: TaskStore):
    class StudioHandler(BaseHTTPRequestHandler):
        server_version = "VideoFactoryStudio/1.0"
        # 每连接 socket 读超时：防慢速请求（谎报大 Content-Length 后龟速发送）长期占用线程。
        timeout = 60

        def log_message(self, fmt: str, *args) -> None:
            print(f"[studio] {self.address_string()} - {fmt % args}")

        # ----- 响应助手 -----

        def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_html(self, html: str) -> None:
            data = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_json_body(self) -> dict | None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            if length > MAX_JSON_BYTES:
                return None  # 过大请求体直接拒，不读进内存（防资源耗尽）
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8", errors="replace").lstrip("﻿"))
            except (json.JSONDecodeError, UnicodeError):
                return None
            return body if isinstance(body, dict) else None

        # ----- GET -----

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                if path == "/":
                    self._send_html(studio_ui.render_page())
                    return
                if path == "/api/meta":
                    self._send_json(build_meta())
                    return
                if path == "/api/jobs":
                    self._send_json({"jobs": store.list()})
                    return
                if path.startswith("/api/jobs/"):
                    self._handle_job_detail(path)
                    return
                if path == "/media":
                    self._handle_media(parse_qs(parsed.query))
                    return
                self._send_json({"error": "未找到该路径。"}, HTTPStatus.NOT_FOUND)
            except Exception as exc:  # 兜底 500，避免击穿服务
                self._send_json({"error": f"服务器内部错误：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_HEAD(self) -> None:
            # 部分播放器在带 Range 的 GET 前先用 HEAD 探测；缺省会 501，这里给
            # 首页与 /media 一个只发头不发体的实现（守卫与 GET 完全一致）。
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                return
            if parsed.path == "/media":
                target = Path((parse_qs(parsed.query).get("path") or [""])[0])
                if not str(target) or not _media_allowed(target):
                    self.send_response(HTTPStatus.FORBIDDEN)
                    self.end_headers()
                    return
                if not target.exists() or not target.is_file():
                    self.send_response(HTTPStatus.NOT_FOUND)
                    self.end_headers()
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", _MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream"))
                self.send_header("Content-Length", str(target.stat().st_size))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                return
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()

        def _handle_job_detail(self, path: str) -> None:
            task_id = path.rsplit("/", 1)[-1]
            task = store.get(task_id)
            if task is None:
                self._send_json({"error": "任务不存在。"}, HTTPStatus.NOT_FOUND)
                return
            self._send_json(task)

        def _handle_media(self, query: dict) -> None:
            raw_path = (query.get("path") or [""])[0]
            if not raw_path:
                self._send_json({"error": "缺少 path 参数。"}, HTTPStatus.BAD_REQUEST)
                return
            target = Path(raw_path)
            if not _media_allowed(target):
                self._send_json({"error": "禁止访问 output/music 目录之外的文件。"}, HTTPStatus.FORBIDDEN)
                return
            if not target.exists() or not target.is_file():
                self._send_json({"error": "文件不存在。"}, HTTPStatus.NOT_FOUND)
                return
            self._serve_file(target)

        def _serve_file(self, target: Path) -> None:
            ctype = _MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream")
            size = target.stat().st_size
            range_header = self.headers.get("Range", "")
            # 视频 + 音频都吃 Range：BGM 试听的进度条拖动靠它（2026-07-16）。
            if (
                target.suffix.lower() in (".mp4", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")
                and range_header.startswith("bytes=")
            ):
                self._serve_range(target, size, ctype, range_header)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self._stream_file(target, 0, size)

        def _stream_file(self, target: Path, start: int, length: int) -> None:
            """按 1MB 块流式下发文件片段（2026-07-15 页面播放卡顿修复）。

            此前整段 read 进内存再一次性 write：大 mp4 首字节极慢、吃内存，叠加 60s
            连接超时，表现为"点播放卡顿→回到开头→永远播不完"。流式后首字节毫秒级。
            浏览器拖动进度条/暂停会随手掐断连接（正常行为），断开异常一律静默吞掉。
            """
            try:
                with target.open("rb") as fh:
                    fh.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = fh.read(min(_UPLOAD_CHUNK, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
                pass  # 客户端主动断开，不是服务错误

        def _serve_range(self, target: Path, size: int, ctype: str, header: str) -> None:
            spec = header.split("=", 1)[1].split(",")[0].strip()
            start_s, _, end_s = spec.partition("-")
            try:
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else size - 1
            except ValueError:
                start, end = 0, size - 1
            start = max(0, start)
            end = min(end, size - 1)
            if start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            length = end - start + 1
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            # 浏览器常发 bytes=0-（开区间到文件尾）：整段读内存曾是播放卡死主因，流式下发。
            self._stream_file(target, start, length)

        # ----- POST -----

        def _origin_allowed(self) -> bool:
            """CSRF 防护：状态变更(POST)只接受来自本工作台自身的请求。

            浏览器发起的跨站 POST（恶意网页的表单/fetch，如打 /api/upload 写文件、
            /api/credentials 篡改凭据）必带 Origin 头，其 host 非本机即拒。无 Origin 的
            请求（curl / 本地脚本）放行，保留本地工具既有用法。
            """
            origin = self.headers.get("Origin")
            if not origin:
                return True
            try:
                host = urlparse(origin).hostname
            except ValueError:
                return False
            return host in _ALLOWED_ORIGIN_HOSTS

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if not self._origin_allowed():
                self._send_json(
                    {"error": "跨站请求被拒绝（Origin 校验未通过）。"}, HTTPStatus.FORBIDDEN
                )
                return
            try:
                if path == "/api/credentials":
                    self._handle_credentials()
                    return
                if path == "/api/settings":
                    self._handle_settings()
                    return
                if path == "/api/upload":
                    self._handle_upload(parse_qs(parsed.query))
                    return
                if path == "/api/jobs":
                    self._handle_create_job()
                    return
                if path == "/api/batch/validate":
                    self._handle_batch_validate()
                    return
                if path == "/api/batch/run":
                    self._handle_batch_run()
                    return
                if path == "/api/open-folder":
                    self._handle_open_folder()
                    return
                self._send_json({"error": "未找到该路径。"}, HTTPStatus.NOT_FOUND)
            except Exception as exc:
                self._send_json({"error": f"服务器内部错误：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def _handle_open_folder(self) -> None:
            """在本机资源管理器里打开成片所在文件夹并选中文件（2026-07-16 用户点名，
            替代任务卡上的"复制路径"）。仅限 output 目录内的既有文件——与 /media
            同一道防线，杜绝借本接口探测/唤起任意路径。"""
            body = self._read_json_body()
            if body is None:
                self._send_json({"error": "请求体不是合法 JSON。"}, HTTPStatus.BAD_REQUEST)
                return
            target = Path(str(body.get("path") or ""))
            if not str(target) or not _within_output(target):
                self._send_json({"error": "禁止打开 output 目录之外的路径。"}, HTTPStatus.FORBIDDEN)
                return
            if not target.exists():
                self._send_json({"error": "文件不存在。"}, HTTPStatus.NOT_FOUND)
                return
            try:
                if sys.platform == "win32":
                    # explorer /select, 打开所在目录并选中该文件；explorer 恒返回非零，
                    # 不检查退出码。
                    subprocess.Popen(["explorer", f"/select,{target.resolve()}"])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", "-R", str(target.resolve())])
                else:
                    subprocess.Popen(["xdg-open", str(target.resolve().parent)])
            except OSError as exc:
                self._send_json({"error": f"打开文件夹失败：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send_json({"ok": True})

        def _handle_credentials(self) -> None:
            body = self._read_json_body()
            if body is None:
                self._send_json({"error": "请求体不是合法 JSON。"}, HTTPStatus.BAD_REQUEST)
                return
            name = str(body.get("name") or "").strip()
            value = body.get("value")
            if name not in CREDENTIAL_NAMES:
                self._send_json({"error": "凭据名不在白名单内。"}, HTTPStatus.BAD_REQUEST)
                return
            if not isinstance(value, str) or not value:
                self._send_json({"error": "凭据值不能为空。"}, HTTPStatus.BAD_REQUEST)
                return
            os.environ[name] = value  # 立即生效
            persisted = True
            try:  # 写回 credentials.yaml，下次启动自动加载；写盘失败不影响本次生效
                credentials_store.save_credential(name, value)
            except OSError:
                persisted = False
            # 响应永不回显 value（只回 name/状态）
            self._send_json({"name": name, "set": True, "persisted": persisted})

        def _handle_settings(self) -> None:
            """非敏感设置保存（白名单制）：立即生效（写 env）+ 持久化 settings.yaml。
            值传空 = 恢复内置默认（清 env、清持久化）。"""
            body = self._read_json_body()
            if body is None:
                self._send_json({"error": "请求体不是合法 JSON。"}, HTTPStatus.BAD_REQUEST)
                return
            name = str(body.get("name") or "").strip()
            if name not in settings_store.SETTING_NAMES:
                self._send_json({"error": "设置名不在白名单内。"}, HTTPStatus.BAD_REQUEST)
                return
            value = str(body.get("value") or "").strip()
            if value:
                os.environ[name] = value
            else:
                os.environ.pop(name, None)
            persisted = True
            try:
                settings_store.save_setting(name, value)
            except OSError:
                persisted = False
            # 回显该设置项自己的当前生效值（历史 bug：曾硬编码回显生图提示词）。
            effective = {
                "IMAGE_STYLE_PROMPT": get_style_prompt,
                "REWRITE_STYLE_PROMPT": get_rewrite_style_prompt,
                # 字号回显生效系数（钳位/回落后的数字，字符串化便于前端 number 输入回填）。
                "SUBTITLE_FONT_SIZE": lambda: str(get_subtitle_font_scale()),
                "SUBTITLE_FONT_SIZE_PORTRAIT": lambda: str(get_subtitle_font_scale(True)),
                "SUBTITLE_FONT_SIZE_LANDSCAPE": lambda: str(get_subtitle_font_scale(False)),
                "SUBTITLE_FONT_NAME": get_subtitle_font_name,
            }.get(name, lambda: value)()
            self._send_json({"name": name, "value": effective, "persisted": persisted})

        def _handle_upload(self, query: dict) -> None:
            kind = (query.get("kind") or [""])[0]
            group = (query.get("group") or [""])[0]
            raw_name = (query.get("name") or [""])[0]
            if kind not in UPLOAD_KINDS:
                self._send_json({"error": "上传类型非法（应为 source/bgm/asset）。"}, HTTPStatus.BAD_REQUEST)
                return
            filename = safe_filename(raw_name)
            if not filename:
                self._send_json({"error": "文件名为空或非法。"}, HTTPStatus.BAD_REQUEST)
                return
            if Path(filename).suffix.lower() not in _SAFE_EXTS:
                self._send_json({"error": "不支持的文件类型。"}, HTTPStatus.BAD_REQUEST)
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > MAX_UPLOAD_BYTES:
                self._send_json({"error": "文件超过 4GB 上限。"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            dest_dir = _upload_dir(kind, group)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / filename
            # 体积上限由上方 Content-Length 前置校验执行；_stream_to_file 按声明长度
            # 定量读取，不可能写超（客户端谎报短长度只会短读，不会撑爆磁盘）。
            written = self._stream_to_file(dest, length)
            if written != length:
                # 上传中断（网络断/连接被掐）：删掉半截文件并明确报错，绝不能让
                # 0 字节/残缺文件冒充上传成功——它会一路混到 rewrite 阶段才以
                # ffmpeg Invalid data 的面目炸出来（2026-07-16 实锤事故）。
                try:
                    dest.unlink(missing_ok=True)
                except OSError:
                    pass
                self._send_json(
                    {"error": f"上传中断：只收到 {written}/{length} 字节，请重新上传。"},
                    HTTPStatus.BAD_REQUEST,
                )
                return
            self._send_json({"path": str(dest)})

        def _stream_to_file(self, dest: Path, length: int) -> int:
            remaining = length
            written = 0
            with dest.open("wb") as fh:
                while remaining > 0:
                    chunk = self.rfile.read(min(_UPLOAD_CHUNK, remaining))
                    if not chunk:
                        break
                    fh.write(chunk)
                    written += len(chunk)
                    remaining -= len(chunk)
            return written

        def _handle_create_job(self) -> None:
            body = self._read_json_body()
            if body is None:
                self._send_json({"error": "请求体不是合法 JSON。"}, HTTPStatus.BAD_REQUEST)
                return
            status, payload = register_job(store, body)
            self._send_json(payload, status)

        def _handle_batch_validate(self) -> None:
            body = self._read_json_body()
            if body is None:
                self._send_json({"error": "请求体不是合法 JSON。"}, HTTPStatus.BAD_REQUEST)
                return
            jobs = body.get("jobs")
            if not isinstance(jobs, list):
                self._send_json({"error": "缺少 jobs 数组。"}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"results": validate_batch([j for j in jobs if isinstance(j, dict)])})

        def _handle_batch_run(self) -> None:
            body = self._read_json_body()
            if body is None:
                self._send_json({"error": "请求体不是合法 JSON。"}, HTTPStatus.BAD_REQUEST)
                return
            jobs = body.get("jobs")
            if not isinstance(jobs, list) or not jobs:
                self._send_json({"error": "缺少非空 jobs 数组。"}, HTTPStatus.BAD_REQUEST)
                return
            registered = []
            for raw in jobs:
                if not isinstance(raw, dict):
                    continue
                _, payload = register_job(store, raw)
                registered.append(payload)
            self._send_json({"jobs": registered})

    return StudioHandler


# ---------- 启动 ----------

class _SingleInstanceServer(ThreadingHTTPServer):
    """端口独占版 HTTP 服务。

    HTTPServer 默认 allow_reuse_address=True（SO_REUSEADDR），在 Windows 上这允许
    第二个实例静默绑到同一端口——两个进程各有各的环境变量和任务表，请求随机落到
    其中一个，表现为「网页保存的凭据时灵时不灵」（2026-07-14 真实事故：凭据保存进了
    A 进程，任务却在没凭据的 B 进程里跑，rewrite 白跑 150s 后报无凭据）。
    禁用后第二个实例会响亮地 bind 失败，由 run_server 转成一句人话。
    """

    allow_reuse_address = False


def run_server(port: int = DEFAULT_PORT) -> int:
    for directory in (STUDIO_ROOT, UPLOAD_ROOT, JOBS_ROOT):
        directory.mkdir(parents=True, exist_ok=True)
    loaded = credentials_store.ensure_env_loaded()
    if loaded:
        print(f"已从 credentials.yaml 加载凭据：{'、'.join(loaded)}")
    store = TaskStore()
    try:
        server = _SingleInstanceServer(("127.0.0.1", port), make_handler(store))
    except OSError:
        print(
            f"端口 {port} 已被占用：可能已有一个创作工作台在运行。"
            f"请先关闭旧窗口（或用 --port 换端口）再启动。"
        )
        return 1
    print(f"创作工作台已启动：http://127.0.0.1:{port}/")
    server.serve_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m video_factory.studio",
        description="创作工作台：本地 Web 控制台，图形化驱动批量生产链路。",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口（默认 56090）")
    args = parser.parse_args(argv)
    return run_server(port=args.port)


if __name__ == "__main__":
    raise SystemExit(main())
