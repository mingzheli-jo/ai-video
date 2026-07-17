"""豆包生图图片库测试（全部离线：fake chat_completion + fake urlopen）。"""

import base64
import json

import pytest

from video_factory.image_gen import (
    CATEGORIES,
    FALLBACK_CATEGORY,
    ImageGenError,
    ImagePlanItem,
    build_image_plan,
    ensure_section_images,
    find_reusable,
    generate_image,
    load_index,
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """隔离凭据：默认无 ARK；LLM 给个假 key 让 resolve_llm_provider 能选中 deepseek。"""
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")


REWRITE = {
    "sections": [
        {"title": "第一节", "narration": "手机后台偷跑内存", "visual_hint": "手机特写"},
        {"title": "第二节", "narration": "清理存储空间", "visual_hint": "清理动画"},
    ]
}


def _plan_reply(category="场景"):
    return json.dumps([
        {"prompt": "手机屏幕特写，冷色调，科技感", "category": "物品", "tags": ["手机", "特写", "科技感"]},
        {"prompt": "整洁的桌面场景，暖光", "category": category, "tags": ["桌面", "整洁", "暖光"]},
    ], ensure_ascii=False)


# --- LLM 派生生图需求 ---


def test_build_image_plan_parses_and_normalizes(monkeypatch):
    monkeypatch.setattr("video_factory.llm.chat_completion", lambda s, u, c: _plan_reply("不存在的类目"))
    plan = build_image_plan(REWRITE)
    assert len(plan) == 2
    assert plan[0].category == "物品" and plan[0].tags == ("手机", "特写", "科技感")
    assert plan[1].category == FALLBACK_CATEGORY  # 非法类目回落
    assert all(p.category in CATEGORIES for p in plan)


def test_build_image_plan_count_mismatch_raises(monkeypatch):
    monkeypatch.setattr(
        "video_factory.llm.chat_completion",
        lambda s, u, c: json.dumps([{"prompt": "只有一条", "category": "场景", "tags": []}]),
    )
    with pytest.raises(ImageGenError, match="不匹配"):
        build_image_plan(REWRITE)


# --- 方舟生图 ---


def test_generate_image_requires_ark_key():
    with pytest.raises(ImageGenError, match="ARK_API_KEY"):
        generate_image("测试提示词")


def test_generate_image_posts_bearer_and_decodes_b64(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "ark-test-1")
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            data = base64.b64encode(b"fake-png-bytes").decode("ascii")
            return json.dumps({"data": [{"b64_json": data}]}).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["request"] = request
        return FakeResponse()

    monkeypatch.setattr("video_factory.image_gen.urlopen", fake_urlopen)
    result = generate_image("一张测试图", size="1080x1920")

    assert result == b"fake-png-bytes"
    request = captured["request"]
    assert request.get_header("Authorization") == "Bearer ark-test-1"
    body = json.loads(request.data.decode("utf-8"))
    assert body["size"] == "1080x1920"
    assert body["response_format"] == "b64_json"
    assert body["watermark"] is False


# --- 图库检索复用 ---


def test_find_reusable_matches_category_size_and_tag_overlap(tmp_path):
    root = tmp_path / "图片"
    (root / "物品").mkdir(parents=True)
    (root / "物品" / "img_aaa.png").write_bytes(b"x")
    (root / "物品" / "img_bbb.png").write_bytes(b"x")
    entries = [
        {"file": "物品/img_aaa.png", "category": "物品", "size": "1080x1920",
         "tags": ["手机", "特写"], "created": 1.0},
        {"file": "物品/img_bbb.png", "category": "物品", "size": "1080x1920",
         "tags": ["手机", "特写", "科技感"], "created": 2.0},
        {"file": "物品/img_gone.png", "category": "物品", "size": "1080x1920",
         "tags": ["手机", "特写", "科技感"], "created": 3.0},  # 文件不存在，须跳过
    ]
    item = ImagePlanItem(0, "提示词", "物品", ("手机", "特写", "科技感"))
    hit = find_reusable(item, "1080x1920", entries, root)
    assert hit is not None and hit.name == "img_bbb.png"  # 重合度最高且文件存在

    # 尺寸不同不复用；重合不足不复用
    assert find_reusable(item, "1920x1080", entries, root) is None
    weak = ImagePlanItem(0, "提示词", "物品", ("无关", "标签"))
    assert find_reusable(weak, "1080x1920", entries, root) is None


# --- 编排：先查库再生图 ---


def test_ensure_section_images_generates_then_reuses(tmp_path, monkeypatch):
    root = tmp_path / "图片"
    monkeypatch.setattr("video_factory.llm.chat_completion", lambda s, u, c: _plan_reply())
    gen_calls = []

    def fake_generate(prompt, size):
        gen_calls.append(prompt)
        return f"png-of-{prompt}".encode("utf-8")

    monkeypatch.setattr("video_factory.image_gen.generate_image", fake_generate)

    # 第一轮：空库 → 两张全部生成入库
    report1 = ensure_section_images(REWRITE, size="1080x1920", library_root=root)
    assert report1["generated"] == 2 and report1["reused"] == 0
    assert len(load_index(root)) == 2
    assert list((root / "物品").glob("img_*.png"))  # 类目文件夹落图
    assert (root / "index.json").exists()  # 登记簿落盘

    # 第二轮：同需求 → 全部库命中，生图零调用
    gen_calls.clear()
    report2 = ensure_section_images(REWRITE, size="1080x1920", library_root=root)
    assert report2["generated"] == 0 and report2["reused"] == 2
    assert gen_calls == []


def test_ensure_section_images_single_failure_does_not_break_batch(tmp_path, monkeypatch):
    root = tmp_path / "图片"
    monkeypatch.setattr("video_factory.llm.chat_completion", lambda s, u, c: _plan_reply())
    calls = []

    def flaky_generate(prompt, size):
        calls.append(prompt)
        if len(calls) == 1:
            raise ImageGenError("模拟限流")
        return b"png-ok"

    monkeypatch.setattr("video_factory.image_gen.generate_image", flaky_generate)
    report = ensure_section_images(REWRITE, size="1080x1920", library_root=root)
    assert report["generated"] == 1
    assert any("生图失败" in w for w in report["warnings"])
    assert len(report["images"]) == 1  # 失败的那节不产出，但不拖垮其余


# ---------- 生图风格提示词（固化+可配置） ----------

def test_get_style_prompt_priority(tmp_path, monkeypatch):
    from video_factory import settings_store
    from video_factory.image_gen import DEFAULT_STYLE_PROMPT, get_style_prompt

    monkeypatch.delenv("IMAGE_STYLE_PROMPT", raising=False)
    # 无 env、无 settings → 内置默认（美式漫画风）
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.yaml")
    assert get_style_prompt() == DEFAULT_STYLE_PROMPT
    assert "美式漫画" in DEFAULT_STYLE_PROMPT
    # settings.yaml 有值 → 用它
    settings_store.save_setting("IMAGE_STYLE_PROMPT", "水墨国风")
    assert get_style_prompt() == "水墨国风"
    # env 最优先
    monkeypatch.setenv("IMAGE_STYLE_PROMPT", "赛博朋克")
    assert get_style_prompt() == "赛博朋克"


def test_settings_store_roundtrip_and_flatten(tmp_path):
    from video_factory import settings_store

    path = tmp_path / "settings.yaml"
    settings_store.save_setting("IMAGE_STYLE_PROMPT", "第一行\n第二行", path=path)
    saved = settings_store.load_settings(path)
    assert saved["IMAGE_STYLE_PROMPT"] == "第一行 第二行"  # 换行折成空格（扁平 YAML 单行）
    settings_store.save_setting("IMAGE_STYLE_PROMPT", "", path=path)
    assert "IMAGE_STYLE_PROMPT" not in settings_store.load_settings(path)  # 清空=恢复默认


def test_ensure_section_images_appends_style_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr("video_factory.llm.chat_completion", lambda s, u, c: _plan_reply())
    monkeypatch.setenv("IMAGE_STYLE_PROMPT", "测试风格XYZ")
    prompts = []

    def fake_generate(prompt, size):
        prompts.append(prompt)
        return b"png"

    monkeypatch.setattr("video_factory.image_gen.generate_image", fake_generate)
    ensure_section_images(REWRITE, size="1080x1920", library_root=tmp_path / "图片")
    assert prompts and all(p.endswith("测试风格XYZ") for p in prompts)  # 风格统一追加在主体之后
    assert all("手机屏幕特写" in prompts[0] for _ in [0])  # 主体提示词仍在


# ---------- 拍级配图（P13 任务B：plan_beats / match_beats_to_library） ----------

def _beats_rewrite():
    return {
        "hook": "四字钩子",
        "sections": [{"title": "第一节", "narration": "这一节口播文案正好二十个字用来切拍测试", "visual_hint": ""}],
    }


def test_plan_beats_math_and_narration_coverage():
    from video_factory.image_gen import plan_beats

    beats = plan_beats(_beats_rewrite(), [8.0, 12.0], beat_seconds=5.0)
    # hook ceil(8/5)=2 拍×4s；第一节 ceil(12/5)=3 拍×4s → 共 5 拍
    assert len(beats) == 5
    assert [b.global_index for b in beats] == [0, 1, 2, 3, 4]
    assert all(abs(b.duration - 4.0) < 1e-6 for b in beats)
    # 节文案按字符均分到拍：拼回去必须无丢字
    sec_beats = [b for b in beats if b.section_index == 1]
    assert "".join(b.narration_slice for b in sec_beats) == "这一节口播文案正好二十个字用来切拍测试"


def test_plan_beats_zero_duration_section_gets_one_beat():
    from video_factory.image_gen import plan_beats

    beats = plan_beats(_beats_rewrite(), [0.0, 10.0], beat_seconds=5.0)
    hook_beats = [b for b in beats if b.section_index == 0]
    assert len(hook_beats) == 1  # 0 秒节退化 1 拍


# ---------- 变长拍：plan_beats_from_timeline（P16 二期，卡话切） ----------

def _sent(text, start, end):
    return {"text": text, "start": start, "end": end}


def _mock_split_on_pipe(monkeypatch):
    """把 split_sentences 换成按 | 切分：测试用它精确控制每节句数，不依赖真实分句规则。"""
    monkeypatch.setattr(
        "video_factory.subtitles.split_sentences",
        lambda text: [p for p in str(text).split("|") if p],
    )


def test_plan_beats_from_timeline_accumulates_to_min(monkeypatch):
    """顺序累计句子，累计时长 ≥min_beat（2.5s）即封一拍。"""
    from video_factory.image_gen import plan_beats_from_timeline

    _mock_split_on_pipe(monkeypatch)
    rewrite = {"hook": "A|B|C|D"}  # 4 句
    sents = [_sent("甲", 0, 1.5), _sent("乙", 1.5, 3.0),
             _sent("丙", 3.0, 4.5), _sent("丁", 4.5, 6.0)]
    beats = plan_beats_from_timeline(rewrite, sents)
    assert len(beats) == 2  # 甲+乙=3.0 封一拍；丙+丁=3.0 封一拍
    assert [round(b.duration, 3) for b in beats] == [3.0, 3.0]
    assert beats[0].narration_slice == "甲乙" and beats[1].narration_slice == "丙丁"
    assert [b.global_index for b in beats] == [0, 1]
    assert all(b.section_index == 0 for b in beats)


def test_plan_beats_from_timeline_splits_over_long_sentence(monkeypatch):
    """单句 >max_beat（8s）拆成 ceil(d/max) 等份子拍。"""
    from video_factory.image_gen import plan_beats_from_timeline

    _mock_split_on_pipe(monkeypatch)
    rewrite = {"hook": "X"}
    sents = [_sent("超长句", 0, 18.0)]  # 18s > 8s → ceil(18/8)=3 份
    beats = plan_beats_from_timeline(rewrite, sents)
    assert len(beats) == 3
    assert all(abs(b.duration - 6.0) < 1e-6 for b in beats)  # 18/3=6
    assert all(b.narration_slice == "超长句" for b in beats)


def test_plan_beats_from_timeline_merges_tail_remnant(monkeypatch):
    """节尾不足 min_beat 的残拍并入前一拍。"""
    from video_factory.image_gen import plan_beats_from_timeline

    _mock_split_on_pipe(monkeypatch)
    rewrite = {"hook": "A|B"}
    sents = [_sent("甲", 0, 3.0), _sent("乙", 3.0, 4.0)]  # 甲封拍(3.0)，乙(1.0)残→并入
    beats = plan_beats_from_timeline(rewrite, sents)
    assert len(beats) == 1
    assert abs(beats[0].duration - 4.0) < 1e-6  # 3.0+1.0 并入
    assert beats[0].narration_slice == "甲乙"


def test_plan_beats_from_timeline_keeps_lone_short_beat(monkeypatch):
    """节首句独拍且不足 min_beat 时保留（该节仅此一拍，无前一拍可并）。"""
    from video_factory.image_gen import plan_beats_from_timeline

    _mock_split_on_pipe(monkeypatch)
    rewrite = {"hook": "A"}
    sents = [_sent("甲", 0, 1.0)]  # 1.0 < 2.5，但独此一拍
    beats = plan_beats_from_timeline(rewrite, sents)
    assert len(beats) == 1
    assert abs(beats[0].duration - 1.0) < 1e-6


def test_plan_beats_from_timeline_count_mismatch_returns_none(monkeypatch):
    """每节句数之和 != timeline 句数（分句口径漂移）→ 返回 None 让调用方回落 5s 路径。"""
    from video_factory.image_gen import plan_beats_from_timeline

    _mock_split_on_pipe(monkeypatch)
    rewrite = {"hook": "A|B"}  # 期望 2 句
    sents = [_sent("甲", 0, 2.0), _sent("乙", 2.0, 4.0), _sent("丙", 4.0, 6.0)]  # 却 3 句
    assert plan_beats_from_timeline(rewrite, sents) is None


def test_plan_beats_from_timeline_empty_sentences_returns_none(monkeypatch):
    from video_factory.image_gen import plan_beats_from_timeline

    _mock_split_on_pipe(monkeypatch)
    assert plan_beats_from_timeline({"hook": "A"}, []) is None


def test_plan_beats_from_timeline_maps_sentences_to_sections(monkeypatch):
    """多节：timeline 句按每节句数对号入座，section_index/section_title 正确归属。"""
    from video_factory.image_gen import plan_beats_from_timeline

    _mock_split_on_pipe(monkeypatch)
    rewrite = {
        "hook": "h",  # 1 句
        "sections": [
            {"title": "第一节", "narration": "a|b"},  # 2 句
            {"title": "第二节", "narration": "c"},     # 1 句
        ],
    }
    sents = [_sent("钩", 0, 3.0),                      # hook 节
             _sent("甲", 3.0, 4.5), _sent("乙", 4.5, 6.0),  # 第一节：1.5+1.5=3.0 一拍
             _sent("丙", 6.0, 9.0)]                     # 第二节：3.0 一拍
    beats = plan_beats_from_timeline(rewrite, sents)
    assert [b.section_index for b in beats] == [0, 1, 2]
    assert [b.section_title for b in beats] == ["hook", "第一节", "第二节"]
    assert [b.global_index for b in beats] == [0, 1, 2]
    # 全片 Σ拍时长 = Σ句时长（真实音频跨度守恒）
    assert abs(sum(b.duration for b in beats) - 9.0) < 1e-6


def test_match_beats_returns_none_without_credentials(monkeypatch):
    from video_factory.image_gen import match_beats_to_library, plan_beats

    for env in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    beats = plan_beats(_beats_rewrite(), [5.0, 5.0])
    assert match_beats_to_library(beats, []) is None  # 无凭据 → 回落每节1图


def test_match_beats_dedupes_repeated_library_file(monkeypatch):
    """硬约束：LLM 把同一张库图分给两拍时，后出现的一拍必须改为 generate。"""
    import json as _json

    from video_factory.image_gen import match_beats_to_library, plan_beats

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    beats = plan_beats(_beats_rewrite(), [5.0, 5.0])  # 2 拍
    reply = _json.dumps([
        {"beat_index": 0, "action": "reuse", "file": "场景/img_a.png", "prompt": ""},
        {"beat_index": 1, "action": "reuse", "file": "场景/img_a.png", "prompt": ""},
    ])
    monkeypatch.setattr("video_factory.llm.chat_completion", lambda s, u, c: reply)
    lib = [{"file": "场景/img_a.png", "tags": ["测试"], "prompt": "x"}]
    result = match_beats_to_library(beats, lib)
    assert result is not None
    assert result[0]["action"] == "reuse"
    assert result[1]["action"] == "generate"  # 重复被代码端强制改生成


def test_match_beats_count_mismatch_falls_back_none(monkeypatch):
    from video_factory.image_gen import match_beats_to_library, plan_beats

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    beats = plan_beats(_beats_rewrite(), [5.0, 5.0])
    monkeypatch.setattr("video_factory.llm.chat_completion", lambda s, u, c: "[]")  # 条数不符
    assert match_beats_to_library(beats, []) is None


# ---------- 拍级生图入库 + 元数据（2026-07-15 修复回归） ----------

def test_ingest_generated_image_stores_and_registers(tmp_path):
    from video_factory.image_gen import ingest_generated_image, load_index

    path = ingest_generated_image(
        b"fake-png-bytes", prompt="城市夜景", category="场景", tags=["城市", "夜晚"],
        size="1440x2560", library_root=tmp_path,
    )
    assert path.exists() and path.parent.name == "场景"     # 落到类目文件夹
    entries = load_index(tmp_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["category"] == "场景" and entry["tags"] == ["城市", "夜晚"]
    assert entry["size"] == "1440x2560" and entry["prompt"] == "城市夜景"


def test_ingest_generated_image_normalizes_bad_category(tmp_path):
    from video_factory.image_gen import ingest_generated_image, load_index

    ingest_generated_image(b"x", prompt="p", category="不存在的类目", tags=None,
                           size="1440x2560", library_root=tmp_path)
    assert load_index(tmp_path)[0]["category"] == "场景"    # 非法类目回落"场景"


def test_beat_match_parser_passes_category_and_tags(monkeypatch):
    import json as _json

    from video_factory.image_gen import match_beats_to_library, plan_beats

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    rewrite = {"hook": "钩子", "sections": [{"title": "一", "narration": "内容" * 10, "visual_hint": ""}]}
    beats = plan_beats(rewrite, [5.0, 5.0])
    reply = _json.dumps([
        {"beat_index": 0, "action": "generate", "file": "", "prompt": "画面A",
         "category": "人物", "tags": ["西装", "办公室"]},
        {"beat_index": 1, "action": "generate", "file": "", "prompt": "画面B",
         "category": "瞎写的", "tags": "不是数组"},
    ])
    monkeypatch.setattr("video_factory.llm.chat_completion", lambda s, u, c: reply)
    result = match_beats_to_library(beats, [])
    assert result[0]["category"] == "人物" and result[0]["tags"] == ["西装", "办公室"]
    assert result[1]["category"] == "场景" and result[1]["tags"] == []  # 宽容归一


# ---------- 生图网络瞬时故障（2026-07-15 IncompleteRead 整单报废事故回归） ----------

class _FakeArkResponse:
    def __init__(self, payload_bytes):
        self._payload = payload_bytes

    def read(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _ok_ark_payload():
    import base64 as _b64
    import json as _json

    return _json.dumps({"data": [{"b64_json": _b64.b64encode(b"img-bytes").decode()}]}).encode()


def test_generate_image_retries_once_on_incomplete_read(monkeypatch):
    """半途断连（IncompleteRead）重试一次成功 → 返回图片字节，不炸。"""
    from http.client import IncompleteRead

    from video_factory import image_gen

    monkeypatch.setenv("ARK_API_KEY", "ak-test")
    responses = iter([
        _FakeArkResponse(IncompleteRead(b"x" * 967679, 510583)),
        _FakeArkResponse(_ok_ark_payload()),
    ])
    monkeypatch.setattr(image_gen, "urlopen", lambda req, timeout: next(responses))
    assert image_gen.generate_image("城市夜景") == b"img-bytes"


def test_generate_image_persistent_network_failure_raises_imagegenerror(monkeypatch):
    """重试后仍断连 → 必须归一为 ImageGenError（RuntimeError 族），
    batch 拍级循环才能接住并跳过该拍，绝不再裸异常击穿整单。"""
    from http.client import IncompleteRead

    from video_factory import image_gen
    from video_factory.image_gen import ImageGenError

    monkeypatch.setenv("ARK_API_KEY", "ak-test")
    monkeypatch.setattr(
        image_gen, "urlopen",
        lambda req, timeout: _FakeArkResponse(IncompleteRead(b"x", 10)),
    )
    with pytest.raises(ImageGenError, match="网络失败"):
        image_gen.generate_image("城市夜景")
    assert isinstance(ImageGenError("x"), RuntimeError)  # batch 的 except RuntimeError 接得住


# ---------- 二级检索：本地粗排 → LLM 精选（P15：库将涨到千级） ----------

def _mk_entries(n, tag="无关", prompt="无关画面描述", created_base=1000.0):
    return [
        {"file": f"场景/img_{i:04d}.png", "category": "场景", "tags": [f"{tag}{i}"],
         "prompt": prompt, "size": "2560x1440", "created": created_base + i}
        for i in range(n)
    ]


def test_prefilter_passthrough_when_library_small():
    from video_factory.image_gen import _PREFILTER_CAP, _prefilter_library, plan_beats

    beats = plan_beats(_beats_rewrite(), [5.0, 5.0])
    entries = _mk_entries(100)  # ≤150：全量直喂，顺序不动
    assert _prefilter_library(beats, entries) == entries
    assert len(entries) < _PREFILTER_CAP + 1


def test_prefilter_surfaces_relevant_entry_from_large_library():
    from video_factory.image_gen import _PREFILTER_CAP, _prefilter_library, plan_beats

    rewrite = {"hook": "钩子", "sections": [
        {"title": "情绪", "narration": "情绪失控的深夜加班场景让人崩溃", "visual_hint": ""}]}
    beats = plan_beats(rewrite, [5.0, 10.0])
    entries = _mk_entries(400)  # 400 张无关图
    hit = {"file": "情绪氛围/img_hit.png", "category": "情绪氛围", "tags": ["深夜加班", "情绪"],
           "prompt": "深夜办公室情绪崩溃的男人", "size": "2560x1440", "created": 1.0}
    entries.insert(0, hit)  # 放最前（旧实现只取最老 50 条时它反而能中——放哪都该中才对）
    result = _prefilter_library(beats, entries)
    assert len(result) <= _PREFILTER_CAP           # 上限收口
    assert any(e["file"] == "情绪氛围/img_hit.png" for e in result)  # 相关图必须浮出
    assert result[0]["file"] == "情绪氛围/img_hit.png"  # 且按分数排第一（标签+双字重合最高）


def test_prefilter_cold_start_falls_back_to_most_recent():
    from video_factory.image_gen import _PREFILTER_COLD_FALLBACK, _prefilter_library, plan_beats

    rewrite = {"hook": "钩子", "sections": [{"title": "甲", "narration": "乙丙", "visual_hint": ""}]}
    beats = plan_beats(rewrite, [5.0, 5.0])
    entries = _mk_entries(300, tag="XYZW", prompt="qqqq")  # 全部与文案零相关
    result = _prefilter_library(beats, entries)
    assert len(result) == _PREFILTER_COLD_FALLBACK
    # 必须是最新入库的（created 最大），绝不能像旧实现取最老的
    assert result[0]["created"] == max(e["created"] for e in entries)


def test_match_beats_feeds_prefiltered_candidates_to_llm(monkeypatch):
    import json as _json

    from video_factory.image_gen import _PREFILTER_CAP, match_beats_to_library, plan_beats

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    beats = plan_beats(_beats_rewrite(), [5.0, 5.0])
    captured = {}

    def fake_chat(system, user, config):
        captured["payload"] = _json.loads(user)
        return _json.dumps([
            {"beat_index": b.global_index, "action": "generate", "file": "",
             "prompt": "画面", "category": "场景", "tags": []}
            for b in beats
        ])

    monkeypatch.setattr("video_factory.llm.chat_completion", fake_chat)
    result = match_beats_to_library(beats, _mk_entries(500))  # 500 张库
    assert result is not None
    assert len(captured["payload"]["library"]) <= _PREFILTER_CAP  # LLM 只看到粗排候选


# ---- 生图模型可配 + 尺寸随模型适配（2026-07-17 切版本吃免费额度） ----


def test_fit_size_for_model_clamps_by_family():
    from video_factory.image_gen import _fit_size_for_model

    # 3.0 上限 2048：4.0 的 2K 尺寸等比缩进盒内（8 的倍数）
    assert _fit_size_for_model("1440x2560", "doubao-seedream-3-0-t2i-250415") == "1152x2048"
    assert _fit_size_for_model("2560x1440", "doubao-seedream-3-0-t2i-250415") == "2048x1152"
    assert _fit_size_for_model("2048x2048", "doubao-seedream-3-0-t2i-250415") == "2048x2048"
    # 4.0：本就合规，原样
    assert _fit_size_for_model("1440x2560", "doubao-seedream-4-0-250828") == "1440x2560"
    # 未知模型族/非法格式：原样放行（不猜未知模型的约束）
    assert _fit_size_for_model("1440x2560", "some-future-model-5-0") == "1440x2560"
    assert _fit_size_for_model("原样", "doubao-seedream-3-0-t2i-250415") == "原样"


def test_get_image_model_chain(monkeypatch):
    from video_factory.image_gen import ARK_IMAGE_DEFAULT_MODEL, get_image_model

    # 默认：无 env 无 settings
    monkeypatch.delenv("ARK_IMAGE_MODEL", raising=False)
    monkeypatch.setattr("video_factory.settings_store.load_settings", lambda: {})
    assert get_image_model() == ARK_IMAGE_DEFAULT_MODEL
    # settings.yaml 兜底
    monkeypatch.setattr(
        "video_factory.settings_store.load_settings",
        lambda: {"ARK_IMAGE_MODEL": "doubao-seedream-3-0-t2i-250415"},
    )
    assert get_image_model() == "doubao-seedream-3-0-t2i-250415"
    # 环境变量最高优先
    monkeypatch.setenv("ARK_IMAGE_MODEL", "doubao-seedream-4-0-250828")
    assert get_image_model() == "doubao-seedream-4-0-250828"


def test_generate_image_uses_configured_model_and_adapted_size(monkeypatch):
    import base64 as _b64

    monkeypatch.setenv("ARK_API_KEY", "ark-test-2")
    monkeypatch.setenv("ARK_IMAGE_MODEL", "doubao-seedream-3-0-t2i-250415")
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            data = _b64.b64encode(b"png").decode("ascii")
            return json.dumps({"data": [{"b64_json": data}]}).encode("utf-8")

    monkeypatch.setattr(
        "video_factory.image_gen.urlopen",
        lambda request, timeout: captured.update(request=request) or FakeResponse(),
    )
    generate_image("测试", size="1440x2560")

    body = json.loads(captured["request"].data.decode("utf-8"))
    # 模型取自配置；尺寸随 3.0 上限自动等比适配，不再被服务端整单拒掉
    assert body["model"] == "doubao-seedream-3-0-t2i-250415"
    assert body["size"] == "1152x2048"


# ---- 画幅铁闸（2026-07-17 实锤 studio_0717_223252：16:9 成片混进 9:16 库图） ----


def test_same_aspect_cross_model_and_orientation():
    from video_factory.image_gen import _same_aspect

    # 跨模型同画幅互认（都是 9:16 / 都是 16:9）
    assert _same_aspect("1440x2560", "1152x2048")
    assert _same_aspect("2560x1440", "2048x1152")
    # 横竖/异画幅绝不互认
    assert not _same_aspect("1440x2560", "2560x1440")
    assert not _same_aspect("2048x2048", "1536x2048")
    # 缺失/非法一律 False（宁缺勿滥）
    assert not _same_aspect("", "2560x1440")
    assert not _same_aspect("坏值", "2560x1440")


def test_image_file_matches_aspect_reads_real_pixels(tmp_path):
    from PIL import Image

    from video_factory.image_gen import image_file_matches_aspect

    portrait = tmp_path / "p.png"
    Image.new("RGB", (90, 160)).save(portrait)
    landscape = tmp_path / "l.png"
    Image.new("RGB", (160, 90)).save(landscape)

    assert image_file_matches_aspect(landscape, "2560x1440")
    assert not image_file_matches_aspect(portrait, "2560x1440")
    assert image_file_matches_aspect(portrait, "1440x2560")
    # 读图失败视为不符
    broken = tmp_path / "b.png"
    broken.write_bytes(b"not-an-image")
    assert not image_file_matches_aspect(broken, "2560x1440")


def test_prefilter_library_filters_by_target_aspect():
    from video_factory.image_gen import _prefilter_library

    entries = [
        {"file": "a.png", "size": "1440x2560", "prompt": "竖图", "tags": []},
        {"file": "b.png", "size": "2560x1440", "prompt": "横图", "tags": []},
        {"file": "c.png", "size": "2048x1152", "prompt": "横图2", "tags": []},
        {"file": "d.png", "prompt": "无尺寸旧条目", "tags": []},
    ]
    kept = _prefilter_library([], entries, target_size="2560x1440")
    files = {e["file"] for e in kept}
    # 只留 16:9；竖图与无 size 的旧条目一并剔除（严格执行）
    assert files == {"b.png", "c.png"}
    # 不传 target_size 维持旧行为（全量）
    assert len(_prefilter_library([], entries)) == 4
