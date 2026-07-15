"""阶段错误落盘/回读（P11 排障可见性）。

背景（2026-07-14 真实事故）：各阶段 main() 失败时只 print 错误 + return 1，print 落在
studio 服务的终端里，任务看板只能显示「XX 阶段返回非 0 退出码：1」——用户对着看板毫无
线索，排障要靠猜。又因 studio 的任务在多个后台线程里并发跑，进程级的 stdout 重定向
（contextlib.redirect_stdout）会互相污染，不可用。

方案：失败的阶段把错误文案写到自己输出目录的 <stage>_error.txt（每任务一目录，天然
线程安全）；batch.run_job 看到非 0 退出码时回读该文件，把真实原因拼进 JobReport.error，
任务看板就能直接显示「改写失败：未配置任何 LLM 凭据…」这样的根因。

写盘失败绝不能反过来打断阶段本身的失败路径（那只是排障增强），所以全部静默兜底。
"""

from __future__ import annotations

from pathlib import Path

_SUFFIX = "_error.txt"


def error_filename(stage: str) -> str:
    return f"{stage}{_SUFFIX}"


def write_stage_error(output_dir: Path | str | None, stage: str, message: str) -> None:
    """把阶段失败原因写到 <output_dir>/<stage>_error.txt。任何写盘失败都静默忽略。"""
    if not output_dir:
        return
    try:
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / error_filename(stage)).write_text(
            str(message).strip() + "\n", encoding="utf-8"
        )
    except OSError:
        pass


def read_stage_error(output_dir: Path | str, stage: str) -> str:
    """回读阶段错误文案（没有/读不到返回空串）。"""
    try:
        return (Path(output_dir) / error_filename(stage)).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def clear_stage_errors(output_dir: Path | str) -> None:
    """清掉目录下全部 *_error.txt（重跑前调用，防上一轮的旧错误串进本轮报告）。"""
    try:
        for path in Path(output_dir).glob(f"*{_SUFFIX}"):
            try:
                path.unlink()
            except OSError:
                pass
    except OSError:
        pass
