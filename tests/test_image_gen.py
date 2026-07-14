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
