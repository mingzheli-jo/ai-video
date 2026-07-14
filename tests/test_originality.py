import json

import video_factory.originality as originality
from video_factory.originality import (
    build_originality_recommendations,
    classify_originality_risk,
    parse_edl_source_reuse,
    text_overlap_ratio,
    write_originality_report,
)


def test_text_overlap_ratio_uses_normalized_tokens():
    source = "用 CC Switch 统一管理 Codex DeepSeek 和 API Key 配置流程"
    output = "CC Switch 可以管理 Codex、DeepSeek、API Key 的配置"

    assert text_overlap_ratio(source, output) > 0.55


def test_parse_edl_source_reuse_estimates_direct_source_usage(tmp_path):
    edl = tmp_path / "edit_decision_list.md"
    edl.write_text(
        """
| # | Source In | Duration | Lens | Edit Intent |
|---|---:|---:|---:|---|
| 1 | 00:00 | 00:30 | 1.00x | 保留开场 |
| 2 | 00:40 | 00:20 | 1.10x | 保留配置 |
""",
        encoding="utf-8",
    )

    reuse = parse_edl_source_reuse(edl, output_duration=55.0)

    assert reuse == {"source_segment_duration": 50.0, "source_reuse_ratio": 0.9091, "segment_count": 2}


def test_classify_originality_risk_marks_source_recut_as_high_risk():
    result = classify_originality_risk(
        visual_similarity=0.87,
        audio_reuse_ratio=0.94,
        text_overlap_ratio=0.88,
        source_reuse_ratio=0.90,
        duration_retention=0.90,
    )

    assert result["risk_level"] == "high"
    assert result["similarity_score"] >= 85
    assert "高度同源" in result["risk_reason"]


def test_originality_recommendations_prioritize_legitimate_originality():
    recommendations = build_originality_recommendations(
        {
            "risk_level": "high",
            "visual_similarity": 0.86,
            "audio_reuse_ratio": 0.91,
            "text_overlap_ratio": 0.82,
            "source_reuse_ratio": 0.88,
        }
    )

    joined = " ".join(recommendations)
    assert "自有画面" in joined
    assert "原创解说" in joined
    assert "不要以重剪版直接发布" in joined


def test_write_originality_report_writes_risk_payload(tmp_path):
    output = tmp_path / "originality_report.json"

    report = write_originality_report(
        output,
        {
            "visual_similarity": 0.87,
            "audio_reuse_ratio": 0.94,
            "text_overlap_ratio": 0.88,
            "source_reuse_ratio": 0.90,
            "duration_retention": 0.90,
        },
    )

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data == report
    assert data["risk_level"] == "high"
    assert data["metrics"]["audio_reuse_ratio"] == 0.94


def test_build_originality_report_prefers_audio_fingerprint_over_edl_proxy(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    output_video = tmp_path / "release.mp4"
    report_path = tmp_path / "originality_report.json"
    edl = tmp_path / "edit_decision_list.md"
    edl.write_text(
        """
| # | Source In | Duration | Lens | Edit Intent |
|---|---:|---:|---:|---|
| 1 | 00:00 | 00:50 | 1.00x | 保留开场 |
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(originality, "_probe_duration", lambda path: 100.0 if path == source else 80.0)
    monkeypatch.setattr(originality, "_estimate_visual_similarity", lambda *args, **kwargs: 0.30)
    monkeypatch.setattr(originality, "_estimate_direct_audio_reuse", lambda *args, **kwargs: 0.12)
    monkeypatch.setattr(originality, "_read_sidecar_text", lambda path: "")

    report = originality.build_originality_report(source, output_video, report_path, edl_path=edl)

    assert report["metrics"]["source_reuse_ratio"] == 0.625
    assert report["metrics"]["audio_reuse_ratio"] == 0.12
    assert report["metrics"]["audio_reuse_provider"] == "audio_fingerprint"
    assert report["metrics"]["text_overlap_ratio"] == 0.12
