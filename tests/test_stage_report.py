"""stage_report 单测：阶段错误落盘/回读/清理（P11 排障可见性）。"""

from video_factory import stage_report


def test_write_then_read_roundtrip(tmp_path):
    stage_report.write_stage_error(tmp_path, "rewrite", "改写失败：未配置任何 LLM 凭据")
    assert stage_report.read_stage_error(tmp_path, "rewrite") == "改写失败：未配置任何 LLM 凭据"


def test_read_missing_returns_empty(tmp_path):
    assert stage_report.read_stage_error(tmp_path, "rewrite") == ""
    assert stage_report.read_stage_error(tmp_path / "nope", "rewrite") == ""


def test_write_creates_output_dir(tmp_path):
    target = tmp_path / "deep" / "job"
    stage_report.write_stage_error(target, "assemble", "拼装失败：x")
    assert (target / "assemble_error.txt").exists()


def test_write_with_empty_output_dir_is_noop():
    stage_report.write_stage_error("", "rewrite", "x")  # 不抛错即通过
    stage_report.write_stage_error(None, "rewrite", "x")


def test_clear_stage_errors_removes_all(tmp_path):
    stage_report.write_stage_error(tmp_path, "rewrite", "a")
    stage_report.write_stage_error(tmp_path, "assemble", "b")
    (tmp_path / "release.mp4").write_text("keep", encoding="utf-8")
    stage_report.clear_stage_errors(tmp_path)
    assert not list(tmp_path.glob("*_error.txt"))
    assert (tmp_path / "release.mp4").exists()  # 其它文件不动


def test_clear_on_missing_dir_is_noop(tmp_path):
    stage_report.clear_stage_errors(tmp_path / "nope")  # 不抛错即通过
