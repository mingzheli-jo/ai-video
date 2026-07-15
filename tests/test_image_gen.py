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
