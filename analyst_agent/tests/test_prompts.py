"""The short answer: a decision first, numbers a human can read."""
import pytest

from analyst_agent import indicators, prompts, verdict
from analyst_agent.tests.conftest import make_df


def _case(trend=0.6, seed=11, frame="1h"):
    facts = indicators.analyze(make_df(trend=trend, seed=seed), frame)
    facts["frame_label"] = "ساعة"
    facts["horizon"] = {"label": "ساعتين", "bars": 2, "minutes": 120}
    return facts, verdict.decide(facts, horizon_bars=2).to_dict()


@pytest.mark.parametrize("value,price,expected", [
    (76673.4, 76673.4, "76,673"),
    (1.3412, 1.3412, "1.3412"),
    (185.234, 185.234, "185.23"),
    (None, 1.0, "-"),
])
def test_prices_are_formatted_for_a_human(value, price, expected):
    assert prompts._fmt(value, price) == expected


def test_action_line_reflects_the_setup():
    assert "عرضي" in prompts._action_line({"side": "none", "entry": None})
    assert "الانتظار" in prompts._action_line(
        {"side": "long", "entry": 1.0, "rr": 0.8, "conviction": 80, "conflicts": []})
    assert "قوية" in prompts._action_line(
        {"side": "long", "entry": 1.0, "rr": 2.0, "conviction": 70, "conflicts": []})
    assert "حجم صغير" in prompts._action_line(
        {"side": "long", "entry": 1.0, "rr": 2.0, "conviction": 40, "conflicts": []})
    assert "الانتظار" in prompts._action_line(
        {"side": "long", "entry": 1.0, "rr": 2.0, "conviction": 80,
         "conflicts": ["أ", "ب"]})


def test_simple_text_fits_a_caption_and_leads_with_the_call():
    facts, call = _case()
    text = prompts.simple_text(symbol="NVDA", frame_label="ساعة", facts=facts, verdict=call)
    assert len(text) <= 1024
    assert text.splitlines()[0].endswith("%")
    assert "📊" in text


def test_simple_text_hides_the_jargon():
    facts, call = _case()
    text = prompts.simple_text(symbol="NVDA", frame_label="ساعة", facts=facts, verdict=call)
    for jargon in ("ema_stack", "EMA fast", "lower_low", "percent_b", "di_plus"):
        assert jargon not in text


def test_conflicts_are_trimmed_to_one_line():
    facts, call = _case()
    call["conflicts"] = ["سبب طويل جداً " * 12]
    text = prompts.simple_text(symbol="NVDA", frame_label="ساعة", facts=facts, verdict=call)
    bullet = [line for line in text.splitlines() if line.startswith("•")][0]
    assert len(bullet) <= 62


def test_a_flat_verdict_says_what_to_wait_for():
    facts, call = _case(trend=0.0, seed=13)
    if call["side"] == "none":
        text = prompts.simple_text(symbol="NVDA", frame_label="ساعة", facts=facts,
                                   verdict=call)
        assert "انتظر" in text and "دخول" not in text.split("📊")[0]


@pytest.mark.parametrize("question,expected", [
    ("NVDA بالتفصيل", True), ("تحليل مفصل", True), ("full analysis", True),
    ("NVDA يومي", False), (None, False),
])
def test_detail_request_is_detected(question, expected):
    assert prompts.wants_detail(question) is expected


def test_the_long_form_still_exists():
    facts, call = _case()
    text = prompts.fallback_text(symbol="NVDA", frame_label="ساعة", facts=facts,
                                 verdict=call)
    assert "القراءة الفنية" in text and "المستويات" in text
