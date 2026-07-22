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


def test_main_loads_credentials_yaml_before_llm(monkeypatch, tmp_path):
    """回归（2026-07-14 事故）：CLI 必须在解析 LLM provider 前加载 credentials.yaml——
    此前 CLI 完全不读 yaml，服务启动后才配的 key 在阶段进程里不可见，
    rewrite 白跑 150s whisper 转写后报「未配置任何 LLM 凭据」。"""
    from video_factory import credentials_store
    from video_factory import rewrite as rewrite_mod

    for env in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    called = []
    monkeypatch.setattr(credentials_store, "ensure_env_loaded", lambda: called.append(True) or [])
    # 无凭据时 main 会在 resolve_llm_provider 处失败返回 1——但 ensure_env_loaded 必须已被调用。
    code = rewrite_mod.main(["--text", "原始文案内容", "--output", str(tmp_path)])
    assert code == 1
    assert called == [True]


def test_main_writes_stage_error_file_on_failure(monkeypatch, tmp_path):
    """回归：rewrite 失败时要把原因落盘 rewrite_error.txt，供 batch 带进任务看板。"""
    from video_factory import rewrite as rewrite_mod

    for env in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    code = rewrite_mod.main(["--text", "原始文案内容", "--output", str(tmp_path)])
    assert code == 1
    body = (tmp_path / "rewrite_error.txt").read_text(encoding="utf-8")
    assert "未配置任何 LLM 凭据" in body


def test_main_writes_source_transcript_before_llm(monkeypatch, tmp_path):
    """rewrite 阶段落盘原文案 source_transcript.txt：供核查 AI 改写质量的对照物，
    也保住昂贵的转写成果。即便 LLM 失败也应已落盘（调 LLM 之前先写）。"""
    from video_factory.llm import LLMProviderError
    from video_factory import rewrite as rewrite_mod

    def boom(system, user, config):
        raise LLMProviderError("模拟 LLM 失败")

    monkeypatch.setattr("video_factory.rewrite.chat_completion", boom)
    code = rewrite_mod.main(
        ["--text", "原始视频文案内容", "--provider", "openai", "--output", str(tmp_path)]
    )

    assert code == 1  # LLM 失败，主流程返回 1
    saved = (tmp_path / "source_transcript.txt").read_text(encoding="utf-8")
    assert saved == "原始视频文案内容"  # 但原文案已在调 LLM 之前落盘


# ============================================================
# 任务A：LLM 强调计划 - emphasis 解析兼容性
# ============================================================


from video_factory.rewrite import _parse_emphasis_items  # noqa: E402


def test_parse_emphasis_items_tolerates_none():
    """旧 rewrite.json 无 emphasis 字段 → 空元组（向后兼容）。"""
    assert _parse_emphasis_items(None) == ()


def test_parse_emphasis_items_tolerates_non_list():
    """emphasis 是非列表（如字符串/数字）→ 空元组（宽容处理）。"""
    assert _parse_emphasis_items("keyword") == ()
    assert _parse_emphasis_items(42) == ()


def test_parse_emphasis_items_empty_list():
    """空列表 → 空元组。"""
    assert _parse_emphasis_items([]) == ()


def test_parse_emphasis_items_all_kinds():
    """三种 kind 均被接受，text 正确保留。"""
    raw = [
        {"text": "核心动作", "kind": "keyword"},
        {"text": "3倍收益", "kind": "number"},
        {"text": "坚持就是胜利", "kind": "golden"},
    ]
    result = _parse_emphasis_items(raw)
    assert len(result) == 3
    assert result[0] == {"text": "核心动作", "kind": "keyword"}
    assert result[1] == {"text": "3倍收益", "kind": "number"}
    assert result[2] == {"text": "坚持就是胜利", "kind": "golden"}


def test_parse_emphasis_items_truncates_keyword_to_10():
    """keyword/number 超过 10 字截断到 10 字（Remotion 弹字空间有限）。"""
    raw = [{"text": "这是一段超长的强调文字超过十个字符", "kind": "keyword"}]
    result = _parse_emphasis_items(raw)
    assert len(result) == 1
    assert len(result[0]["text"]) <= 10


def test_parse_emphasis_items_keeps_golden_up_to_24():
    """golden 是照抄的口播原句、用于定位金句卡，保留到 24 字不截成 10（2026-07-22）。"""
    raw = [{"text": "资源比努力更值钱，信息差比苦力更赚钱", "kind": "golden"}]  # 17 字
    result = _parse_emphasis_items(raw)
    assert result[0]["text"] == "资源比努力更值钱，信息差比苦力更赚钱"  # 完整保留
    # 超 24 字仍截断（防异常超长）
    long_golden = [{"text": "金" * 40, "kind": "golden"}]
    assert len(_parse_emphasis_items(long_golden)[0]["text"]) == 24


def test_parse_emphasis_items_caps_at_3():
    """超过 3 条时只保留前 3 条（每节最多 3 个弹字动效）。"""
    raw = [{"text": f"词{i}", "kind": "keyword"} for i in range(6)]
    result = _parse_emphasis_items(raw)
    assert len(result) == 3
    assert result[0]["text"] == "词0"
    assert result[2]["text"] == "词2"


def test_parse_emphasis_items_normalizes_unknown_kind():
    """未知 kind（如 'special'）→ 回落为 'keyword'。"""
    raw = [{"text": "重点词", "kind": "special_unknown"}]
    result = _parse_emphasis_items(raw)
    assert result[0]["kind"] == "keyword"


def test_parse_emphasis_items_skips_non_dict_elements():
    """列表里的非 dict 元素直接跳过（宽容解析）。"""
    raw = ["字符串", None, {"text": "有效词", "kind": "keyword"}, 42]
    result = _parse_emphasis_items(raw)
    assert len(result) == 1
    assert result[0]["text"] == "有效词"


def test_parse_emphasis_items_skips_empty_text():
    """text 为空字符串时跳过该条（空弹字无意义）。"""
    raw = [{"text": "", "kind": "keyword"}, {"text": "  ", "kind": "golden"}, {"text": "好", "kind": "keyword"}]
    result = _parse_emphasis_items(raw)
    assert len(result) == 1
    assert result[0]["text"] == "好"


def test_rewrite_section_emphasis_defaults_to_empty_tuple():
    """RewriteSection 默认 emphasis 为空元组（旧代码创建的 section 向后兼容）。"""
    from video_factory.rewrite import RewriteSection

    section = RewriteSection(index=0, title="标题", narration="口播", visual_hint="画面")
    assert section.emphasis == ()


def test_rewrite_copy_parses_emphasis_from_llm_reply(monkeypatch):
    """LLM 回复带 emphasis 时，解析进 RewriteSection 并可通过 rewrite_result_to_dict 序列化。"""
    reply_with_emphasis = {
        "hook": "三秒钩子。",
        "sections": [
            {
                "title": "第一步",
                "narration": "先把选题定死在一个问题上。",
                "visual_hint": "操作录屏",
                "emphasis": [
                    {"text": "选题定死", "kind": "keyword"},
                    {"text": "1个问题", "kind": "number"},
                ],
            },
            {
                "title": "第二步",
                "narration": "开头直接给结果，不做铺垫。",
                "visual_hint": "数据截图",
                # 无 emphasis 字段 → 向后兼容，应等同于空
            },
        ],
        "publish_titles": ["标题一"],
        "notes": "",
    }
    monkeypatch.setattr(
        "video_factory.rewrite.chat_completion",
        lambda system, user, config: json.dumps(reply_with_emphasis, ensure_ascii=False),
    )
    result = rewrite_copy("原始文案", LLMConfig(provider="openai"), target_duration_seconds=60)

    # 第一节 emphasis 解析正确
    assert len(result.sections[0].emphasis) == 2
    assert result.sections[0].emphasis[0] == {"text": "选题定死", "kind": "keyword"}
    assert result.sections[0].emphasis[1] == {"text": "1个问题", "kind": "number"}
    # 第二节无 emphasis → 空元组（向后兼容）
    assert result.sections[1].emphasis == ()

    # rewrite_result_to_dict 序列化包含 emphasis
    d = rewrite_result_to_dict(result)
    assert d["sections"][0]["emphasis"] == [
        {"text": "选题定死", "kind": "keyword"},
        {"text": "1个问题", "kind": "number"},
    ]
    assert d["sections"][1]["emphasis"] == []


def test_build_rewrite_prompts_includes_emphasis_guidance():
    """system_prompt 包含 emphasis 字段说明（keyword/number/golden 三种 kind）。"""
    system_prompt, _ = build_rewrite_prompts("原始文案", target_duration_seconds=90)
    assert "emphasis" in system_prompt
    assert "keyword" in system_prompt
    assert "number" in system_prompt
    assert "golden" in system_prompt


def test_build_rewrite_prompts_emphasis_guidance_requires_many_verbatim_golden():
    """emphasis 引导（2026-07-22 强化）：全片 5~8 条 golden、照抄口播原句、禁止转述。

    背景：金句卡改为沿片长铺多张（间隔≥35s），弹药来自 LLM 标的 golden；且 golden
    须与口播逐字一致才能在时间轴上定位（旧版压缩转述导致 4 句金句 3 句定位失败）。
    """
    system_prompt, _ = build_rewrite_prompts("原始文案", target_duration_seconds=90)
    assert "5~8 条" in system_prompt        # 全片标 5~8 条 golden（金句卡弹药要足）
    assert "原样照抄" in system_prompt       # 照抄口播原句（可定位）
    assert "禁止压缩或转述" in system_prompt  # 禁止转述（转述会定位失败）
    assert "宁缺勿滥" not in system_prompt   # 旧的抑制性措辞仍不复现


def test_build_rewrite_prompts_drops_opening_hooks_and_strengthens_hook():
    """2026-07-15 定案：LLM 不再另写 opening_hooks（屏幕文字改用 hook 口播原文 1:1），
    换成强化 hook 本身——强钩子要求 + 子句短促（会拆成开屏大字）。"""
    system_prompt, _ = build_rewrite_prompts("原始文案", target_duration_seconds=90)
    assert "opening_hooks" not in system_prompt
    assert "强钩子" in system_prompt
    assert "子句" in system_prompt  # 告知 LLM：hook 子句会拆成开屏大字


# ---------- opening_hooks：开屏钩子序列解析兼容性 ----------

from video_factory.rewrite import _parse_opening_hooks  # noqa: E402


def test_parse_opening_hooks_tolerates_none():
    """旧 rewrite.json 无 opening_hooks 字段 → 空元组（向后兼容）。"""
    assert _parse_opening_hooks(None) == ()


def test_parse_opening_hooks_tolerates_non_list():
    """opening_hooks 是非列表（字符串/数字）→ 空元组（宽容处理）。"""
    assert _parse_opening_hooks("钩子") == ()
    assert _parse_opening_hooks(42) == ()


def test_parse_opening_hooks_parses_and_truncates_and_caps():
    """短句去空、每条截断到 12 字、最多保留 3 条。"""
    raw = ["你敢信吗？", "  ", "这是一条超过十二个字的超长开屏钩子短句", "第三条", "第四条超额"]
    result = _parse_opening_hooks(raw)
    assert result[0] == "你敢信吗？"                       # 正常短句保留
    assert result[1] == "这是一条超过十二个字的超"          # 截断到 12 字
    assert len(result) == 3                                 # 空串跳过 + 上限 3 条
    assert all(len(x) <= 12 for x in result)


def test_rewrite_copy_parses_opening_hooks_and_serializes(monkeypatch, tmp_path):
    """LLM 回复带 opening_hooks 时解析进 RewriteResult 并写入 rewrite.json；缺失时为空。"""
    reply = dict(VALID_REPLY, opening_hooks=["3秒看懂", "你亏在哪？"])
    monkeypatch.setattr(
        "video_factory.rewrite.chat_completion",
        lambda system, user, config: json.dumps(reply, ensure_ascii=False),
    )
    result = rewrite_copy("原始文案", LLMConfig(provider="openai"), target_duration_seconds=60)
    assert result.opening_hooks == ("3秒看懂", "你亏在哪？")
    saved = rewrite_result_to_dict(result)
    assert saved["opening_hooks"] == ["3秒看懂", "你亏在哪？"]

    # 缺 opening_hooks（旧回复）→ 空元组、序列化为空数组
    monkeypatch.setattr(
        "video_factory.rewrite.chat_completion",
        lambda system, user, config: json.dumps(VALID_REPLY, ensure_ascii=False),
    )
    legacy = rewrite_copy("原始文案", LLMConfig(provider="openai"), target_duration_seconds=60)
    assert legacy.opening_hooks == ()
    assert rewrite_result_to_dict(legacy)["opening_hooks"] == []


# ---------- 改写文风指令（P14：DeepSeek 提示词自定义，仿生图 IMAGE_STYLE_PROMPT 机制） ----------

def test_rewrite_style_prompt_priority_env_over_settings(monkeypatch, tmp_path):
    from video_factory import settings_store
    from video_factory.rewrite import REWRITE_STYLE_PROMPT_ENV, get_rewrite_style_prompt

    settings_path = tmp_path / "settings.yaml"
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", settings_path)
    monkeypatch.delenv(REWRITE_STYLE_PROMPT_ENV, raising=False)
    assert get_rewrite_style_prompt() == ""          # 无 env 无文件 → 空（用内置模板）
    settings_store.save_setting(REWRITE_STYLE_PROMPT_ENV, "settings里的指令", path=settings_path)
    assert get_rewrite_style_prompt() == "settings里的指令"   # settings.yaml 生效
    monkeypatch.setenv(REWRITE_STYLE_PROMPT_ENV, "env里的指令")
    assert get_rewrite_style_prompt() == "env里的指令"        # 环境变量优先


def test_build_prompts_injects_custom_style_directive(monkeypatch, tmp_path):
    from video_factory import settings_store
    from video_factory.rewrite import REWRITE_STYLE_PROMPT_ENV, build_rewrite_prompts

    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.yaml")
    monkeypatch.setenv(REWRITE_STYLE_PROMPT_ENV, "多用短句和反问，结尾留悬念")
    system_prompt, _ = build_rewrite_prompts("原始文案")
    assert "全局创作指令" in system_prompt
    assert "多用短句和反问，结尾留悬念" in system_prompt
    assert "JSON 输出格式必须严格遵守" in system_prompt   # 格式硬约束不可被指令推翻


def test_build_prompts_no_directive_block_when_unset(monkeypatch, tmp_path):
    from video_factory import settings_store
    from video_factory.rewrite import REWRITE_STYLE_PROMPT_ENV, build_rewrite_prompts

    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.yaml")
    monkeypatch.delenv(REWRITE_STYLE_PROMPT_ENV, raising=False)
    system_prompt, _ = build_rewrite_prompts("原始文案")
    assert "全局创作指令" not in system_prompt          # 未设置 → 不注入空块
