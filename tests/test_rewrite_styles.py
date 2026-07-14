import pytest

from video_factory.rewrite_styles import (
    DEFAULT_STYLE_KEY,
    STYLES,
    RewriteStyle,
    UnknownStyleError,
    get_style,
)

EXPECTED_KEYS = {
    "general",
    "tutorial",
    "film_recap",
    "seeding",
    "emotion",
    "ranking",
    "hot_take",
}


def test_registry_has_all_seven_styles():
    assert set(STYLES) == EXPECTED_KEYS


@pytest.mark.parametrize("key", sorted(EXPECTED_KEYS))
def test_get_style_returns_registered_style(key):
    style = get_style(key)
    assert isinstance(style, RewriteStyle)
    assert style.key == key
    # 每个槽位都要有实际可进提示词的中文文案，不能是空串。
    assert style.persona.strip()
    assert style.tone.strip()
    assert style.hook_guidance.strip()
    assert style.section_guidance.strip()
    assert style.tts_hint.strip()


def test_get_style_defaults_to_general_when_blank_or_none():
    assert get_style(None).key == DEFAULT_STYLE_KEY
    assert get_style("").key == DEFAULT_STYLE_KEY
    assert get_style("  ").key == DEFAULT_STYLE_KEY


def test_get_style_passes_through_style_instance():
    style = STYLES["emotion"]
    assert get_style(style) is style


def test_get_style_unknown_lists_all_options():
    with pytest.raises(UnknownStyleError) as excinfo:
        get_style("not_a_real_style")
    message = str(excinfo.value)
    assert "not_a_real_style" in message
    for key in EXPECTED_KEYS:
        assert key in message


def test_unknown_style_error_is_value_error():
    assert issubclass(UnknownStyleError, ValueError)


def test_emotion_tts_hint_is_slow_female():
    assert "低速抒情女声" in STYLES["emotion"].tts_hint


def test_seeding_tone_guards_against_absolute_wording():
    # 带货种草模板必须内置广告法规避提示（绝对化用语）。
    assert "广告法" in STYLES["seeding"].tone
