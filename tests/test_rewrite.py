import json

import pytest

from video_factory.llm import LLMConfig
from video_factory.rewrite import (
    RewriteError,
    build_rewrite_prompts,
    load_source_text,
    main,
    parse_rewrite_response,
    rewrite_copy,
    rewrite_result_to_dict,
    write_rewrite_outputs,
)

VALID_REPLY = {
    "hook": "三秒告诉你结论。",
    "sections": [
        {"title": "第一步", "narration": "先把选题定死在一个问题上。", "visual_hint": "操作录屏"},
        {"title": "第二步", "narration": "开头直接给结果，不做铺垫。", "visual_hint": "数据截图"},
    ],
    "publish_titles": ["标题一", "标题二", "标题三"],
    "notes": "结构已重排。",
}


def test_build_rewrite_prompts_contains_char_budget_and_brief():
    system_prompt, user_prompt = build_rewrite_prompts("原始文案", target_duration_seconds=90, brief="面向新手")

    assert "432" in system_prompt  # 90s * 4.8 字/秒（刘飞音色实测校准）
    assert "90 秒" in system_prompt
    assert "面向新手" in user_prompt
    assert "原始文案" in user_prompt


def test_parse_rewrite_response_accepts_fenced_json():
    raw = "```json\n" + json.dumps(VALID_REPLY, ensure_ascii=False) + "\n```"
    assert parse_rewrite_response(raw)["hook"] == "三秒告诉你结论。"


def test_parse_rewrite_response_accepts_surrounding_prose():
    raw = "好的，以下是改写结果：\n" + json.dumps(VALID_REPLY, ensure_ascii=False) + "\n希望有帮助。"
    assert len(parse_rewrite_response(raw)["sections"]) == 2


def test_parse_rewrite_response_rejects_missing_hook():
    body = dict(VALID_REPLY, hook="")
    with pytest.raises(RewriteError, match="hook"):
        parse_rewrite_response(json.dumps(body, ensure_ascii=False))


def test_parse_rewrite_response_rejects_non_dict_section_items():
    body = dict(VALID_REPLY, sections=["不是对象", "也不是对象"])
    with pytest.raises(RewriteError, match="非法元素"):
        parse_rewrite_response(json.dumps(body, ensure_ascii=False))


def test_load_source_text_strips_bom_from_plain_text(tmp_path):
    txt = tmp_path / "input.txt"
    txt.write_bytes("﻿你好\n世界\n".encode("utf-8"))

    assert load_source_text(txt) == "你好\n世界"


def test_parse_rewrite_response_rejects_single_section():
    body = dict(VALID_REPLY, sections=VALID_REPLY["sections"][:1])
    with pytest.raises(RewriteError, match="sections"):
        parse_rewrite_response(json.dumps(body, ensure_ascii=False))


def test_rewrite_copy_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "video_factory.rewrite.chat_completion",
        lambda system, user, config: json.dumps(VALID_REPLY, ensure_ascii=False),
    )

    result = rewrite_copy("原始文案内容", LLMConfig(provider="openai"), target_duration_seconds=60)

    assert result.hook == "三秒告诉你结论。"
    assert [section.title for section in result.sections] == ["第一步", "第二步"]
    assert "先把选题定死" in result.full_voiceover
    assert result.estimated_duration_seconds > 0
    assert result.publish_titles == ("标题一", "标题二", "标题三")

    outputs = write_rewrite_outputs(result, tmp_path)
    saved = json.loads(outputs["rewrite_json"].read_text(encoding="utf-8"))
    assert saved["version"] == "rewrite_v1"
    assert saved["target_duration_seconds"] == 60
    assert outputs["voiceover_txt"].read_text(encoding="utf-8").startswith("三秒告诉你结论。")


def test_rewrite_copy_rejects_empty_source():
    with pytest.raises(RewriteError, match="原始文案为空"):
        rewrite_copy("   ")


# ---------- 时长闭环第一级：字数不足自动扩写 ----------

def _reply_with_narration(narration: str) -> str:
    body = dict(
        VALID_REPLY,
        sections=[
            {"title": "第一步", "narration": narration, "visual_hint": "画面"},
            {"title": "第二步", "narration": narration, "visual_hint": "画面"},
        ],
    )
    return json.dumps(body, ensure_ascii=False)


def test_rewrite_copy_expands_when_copy_too_short(monkeypatch):
    # 首轮欠写（远低于目标85%）→ 自动扩写回炉；扩写达标后停止并记录轮数。
    short = _reply_with_narration("太短了。")
    long = _reply_with_narration("这一节写得足够充实，" * 40)  # 每节约400字，两节共约800字
    calls = []

    def fake_chat(system, user, config):
        calls.append(system)
        return short if len(calls) == 1 else long

    monkeypatch.setattr("video_factory.rewrite.chat_completion", fake_chat)
    result = rewrite_copy("原始文案", LLMConfig(provider="deepseek"), target_duration_seconds=120)

    assert len(calls) == 2  # 首轮 + 1 轮扩写
    assert "扩写" in calls[1]  # 第二次调用是扩写提示词
    assert result.expand_rounds == 1
    # 120s * 4.8 * 0.85 ≈ 490字 → 扩写后 ~800字 达标
    assert result.estimated_duration_seconds >= 120 * 0.85


def test_rewrite_copy_no_expand_when_length_ok(monkeypatch):
    # 首轮字数达标 → 只调一次 LLM，不触发扩写。
    adequate = _reply_with_narration("内容饱满，" * 60)  # 两节共约600字 > 120s*4.8*0.85
    calls = []

    def fake_chat(system, user, config):
        calls.append(system)
        return adequate

    monkeypatch.setattr("video_factory.rewrite.chat_completion", fake_chat)
    result = rewrite_copy("原始文案", LLMConfig(provider="deepseek"), target_duration_seconds=120)

    assert len(calls) == 1
    assert result.expand_rounds == 0


def test_rewrite_copy_expand_keeps_original_when_llm_writes_shorter(monkeypatch):
    # 扩写越写越短 → 止损：保留首轮结果、立即停止（不再烧第二轮）。
    short = _reply_with_narration("首轮内容短。")
    shorter = _reply_with_narration("更短。")
    calls = []

    def fake_chat(system, user, config):
        calls.append(system)
        return short if len(calls) == 1 else shorter

    monkeypatch.setattr("video_factory.rewrite.chat_completion", fake_chat)
    result = rewrite_copy("原始文案", LLMConfig(provider="deepseek"), target_duration_seconds=120)

    assert len(calls) == 2  # 首轮 + 1 次失败的扩写尝试，随即止损
    assert result.expand_rounds == 0  # 保留的是首轮结果
    assert "首轮内容短" in result.full_voiceover


def test_rewrite_copy_expand_survives_llm_error(monkeypatch):
    # 扩写调用抛错 → 不阻断：返回首轮结果（宁短勿废）。
    from video_factory.llm import LLMProviderError

    short = _reply_with_narration("首轮内容短。")
    calls = []

    def fake_chat(system, user, config):
        calls.append(system)
        if len(calls) == 1:
            return short
        raise LLMProviderError("模拟扩写超时")

    monkeypatch.setattr("video_factory.rewrite.chat_completion", fake_chat)
    result = rewrite_copy("原始文案", LLMConfig(provider="deepseek"), target_duration_seconds=120)

    assert "首轮内容短" in result.full_voiceover
    assert result.expand_rounds == 0


def test_rewrite_json_records_expand_rounds(monkeypatch, tmp_path):
    from video_factory.rewrite import write_rewrite_outputs

    short = _reply_with_narration("短。")
    long = _reply_with_narration("足够长的内容，" * 40)
    calls = []
    monkeypatch.setattr(
        "video_factory.rewrite.chat_completion",
        lambda s, u, c: (calls.append(1) or (short if len(calls) == 1 else long)),
    )
    result = rewrite_copy("原始文案", LLMConfig(provider="deepseek"), target_duration_seconds=120)
    saved = json.loads(
        write_rewrite_outputs(result, tmp_path)["rewrite_json"].read_text(encoding="utf-8")
    )
    assert saved["expand_rounds"] == 1


def test_load_source_text_strips_srt_noise(tmp_path):
    srt = tmp_path / "input.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\n大家好\n\n"
        "2\n00:00:03,000 --> 00:00:05,000\n大家好\n\n"
        "3\n00:00:05,000 --> 00:00:08,000\n今天讲三个方法\n",
        encoding="utf-8",
    )

    assert load_source_text(srt) == "大家好\n今天讲三个方法"


def test_load_source_text_strips_vtt_header(tmp_path):
    vtt = tmp_path / "input.vtt"
    vtt.write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n第一句\n\n00:00:03.000 --> 00:00:05.000\n第二句\n",
        encoding="utf-8",
    )

    assert load_source_text(vtt) == "第一句\n第二句"


def test_load_source_text_reads_transcript_json(tmp_path):
    transcript = tmp_path / "transcript.json"
    transcript.write_text(
        json.dumps({"cues": [{"text": "第一句"}, {"text": ""}, {"text": "第二句"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert load_source_text(transcript) == "第一句\n第二句"


def test_load_source_text_routes_media_to_asr(tmp_path, monkeypatch):
    from video_factory import asr

    media = tmp_path / "clip.mp4"
    media.write_bytes(b"fake video")
    seen = {}

    def fake_transcribe_media(path, config):
        seen["path"] = path
        seen["config"] = config
        return asr.ASRResult(text="转写出来的文案", segments=(), provider="faster_whisper", model="small")

    monkeypatch.setattr("video_factory.asr.transcribe_media", fake_transcribe_media)

    text = load_source_text(media, asr.ASRConfig(provider="openai", model="whisper-x"))

    assert text == "转写出来的文案"
    assert seen["path"] == media
    assert seen["config"].provider == "openai"


def test_load_source_text_non_media_untouched(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("非媒体文件不应触发 ASR")

    monkeypatch.setattr("video_factory.asr.transcribe_media", boom)

    txt = tmp_path / "plain.txt"
    txt.write_text("纯文本内容", encoding="utf-8")
    assert load_source_text(txt) == "纯文本内容"


def test_build_rewrite_prompts_default_style_keeps_general_skeleton():
    system_prompt, _ = build_rewrite_prompts("原始文案", target_duration_seconds=90)

    # 通用骨架：JSON schema、字数预算、原创红线、最少小节数都要保留。
    assert "短视频文案改写专家" in system_prompt
    assert "432" in system_prompt  # 90s * 4.8 字/秒（校准后）
    assert '{"hook":' in system_prompt
    assert "不得照抄原文句子" in system_prompt
    assert "sections 至少 2 个" in system_prompt


def test_build_rewrite_prompts_film_recap_has_template_and_skeleton():
    system_prompt, _ = build_rewrite_prompts(
        "原始文案", target_duration_seconds=90, style="film_recap"
    )

    # 影视解说模板特征词。
    assert "悬念叙事解说人" in system_prompt
    assert "万万没想到" in system_prompt
    # 通用骨架仍在。
    assert "432" in system_prompt  # 90s * 4.8 字/秒（校准后）
    assert '{"hook":' in system_prompt
    assert "不得照抄原文句子" in system_prompt
    assert "sections 至少 2 个" in system_prompt


def test_build_rewrite_prompts_unknown_style_raises_with_options():
    with pytest.raises(RewriteError) as excinfo:
        build_rewrite_prompts("原始文案", style="不存在的类型")
    message = str(excinfo.value)
    assert "不存在的类型" in message
    for key in ("general", "film_recap", "seeding"):
        assert key in message


def test_build_rewrite_prompts_brief_overrides_template():
    system_prompt, user_prompt = build_rewrite_prompts(
        "原始文案", brief="必须只讲一个卖点", style="seeding"
    )
    assert "以此为准" in user_prompt
    assert "必须只讲一个卖点" in user_prompt
    # 带货种草模板仍生效。
    assert "真实体验分享者" in system_prompt


def test_rewrite_copy_threads_style_into_result_and_dict(monkeypatch, tmp_path):
    seen = {}

    def fake_chat(system, user, config):
        seen["system"] = system
        return json.dumps(VALID_REPLY, ensure_ascii=False)

    monkeypatch.setattr("video_factory.rewrite.chat_completion", fake_chat)

    result = rewrite_copy("原始文案内容", LLMConfig(provider="openai"), style="emotion")

    assert result.style == "emotion"
    assert "深夜电台主播" in seen["system"]
    saved = rewrite_result_to_dict(result)
    assert saved["style"] == "emotion"

    outputs = write_rewrite_outputs(result, tmp_path)
    on_disk = json.loads(outputs["rewrite_json"].read_text(encoding="utf-8"))
    assert on_disk["style"] == "emotion"


def test_rewrite_copy_defaults_to_general_style(monkeypatch):
    monkeypatch.setattr(
        "video_factory.rewrite.chat_completion",
        lambda system, user, config: json.dumps(VALID_REPLY, ensure_ascii=False),
    )

    result = rewrite_copy("原始文案内容", LLMConfig(provider="openai"))

    assert result.style == "general"


def test_rewrite_copy_rejects_unknown_style():
    with pytest.raises(RewriteError, match="未知内容类型"):
        rewrite_copy("原始文案内容", LLMConfig(provider="openai"), style="乱写")


def test_cli_style_smoke(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        "video_factory.rewrite.chat_completion",
        lambda system, user, config: json.dumps(VALID_REPLY, ensure_ascii=False),
    )

    output_dir = tmp_path / "out"
    exit_code = main(
        [
            "--text",
            "原始文案内容",
            "--style",
            "ranking",
            "--provider",
            "openai",
            "--output",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "盘点榜单" in captured  # style.label
    assert "内容类型" in captured
    saved = json.loads((output_dir / "rewrite.json").read_text(encoding="utf-8"))
    assert saved["style"] == "ranking"


def test_cli_rejects_invalid_style_choice(capsys):
    with pytest.raises(SystemExit):
        main(["--text", "原始文案内容", "--style", "不存在"])
    err = capsys.readouterr().err
    assert "--style" in err


def test_resolve_llm_provider_auto_picks_first_configured(monkeypatch):
    from video_factory.rewrite import resolve_llm_provider

    for env in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dsk-1")
    assert resolve_llm_provider("auto") == "deepseek"  # 只配了 deepseek 就用它
    monkeypatch.setenv("OPENAI_API_KEY", "sk-1")
    assert resolve_llm_provider("auto") == "openai"  # openai 顺位在前
    assert resolve_llm_provider("anthropic") == "anthropic"  # 显式选择直通


def test_resolve_llm_provider_auto_without_any_key_raises(monkeypatch):
    from video_factory.rewrite import resolve_llm_provider

    for env in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    with pytest.raises(RewriteError, match="未配置任何 LLM 凭据"):
        resolve_llm_provider("auto")
