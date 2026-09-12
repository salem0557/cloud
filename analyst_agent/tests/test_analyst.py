import json

import pytest

from analyst_agent import analyst, config, frames, market, prompts, vision
from analyst_agent.market import MarketData
from analyst_agent.tests.conftest import make_df


@pytest.fixture
def offline(monkeypatch):
    """No network: fixed bars, fixed news, no Groq key (template answer)."""
    def fake_load(candidates, frame, with_meta=True):
        symbol = getattr(candidates[0], "symbol", candidates[0])
        data = MarketData(
            symbol=symbol, frame=frame,
            df=make_df(trend=0.5, seed=61),
            context_df=make_df(trend=0.5, seed=62, freq="1D", tz=None),
            daily_df=make_df(trend=0.5, seed=63, n=400, freq="1D", tz=None),
        )
        data.meta = {"name": "Test Co", "exchange": "NasdaqGS", "currency": "USD",
                     "fetched_at": "2026-09-12T12:00:00Z"}
        return data

    monkeypatch.setattr(market, "load", fake_load)
    monkeypatch.setattr(analyst.market, "load", fake_load)
    monkeypatch.setattr(analyst.news_mod, "bundle", lambda symbol, meta=None: {
        "headlines": [{"title": "Test beats estimates", "publisher": "Reuters"}],
        "social": {"messages": ["strong"], "bullish": 5, "bearish": 1, "tilt": "إيجابي"},
        "events": {},
    })
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    return fake_load


def test_text_request_produces_chart_and_analysis(offline):
    answer = analyst.analyze("حلل TSLA فريم 15 دقيقة")
    assert answer.ok
    assert answer.symbol == "TSLA"
    assert answer.frame_key == "15m"
    assert answer.chart_png[:4] == b"\x89PNG"
    assert "الخلاصة" in answer.text and "الخطة" in answer.text
    assert answer.debug["frame_source"] == "مكتوب في الرسالة"


def test_written_frame_beats_the_screenshot(offline, monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(analyst, "vision_read", lambda image: vision.ChartRead(
        is_chart=True, symbol="AAPL", frame_key="1d", symbol_raw="AAPL"))
    answer = analyst.analyze("حلل 15 دقيقة", image=b"\x89PNG")
    assert answer.frame_key == "15m"          # message wins over image
    assert answer.symbol == "AAPL"            # symbol came from the image
    assert answer.debug["symbol_source"] == "الصورة"


def test_screenshot_frame_is_used_when_nothing_is_written(offline, monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(analyst, "vision_read", lambda image: vision.ChartRead(
        is_chart=True, symbol="AAPL", frame_key="4h"))
    answer = analyst.analyze("حلل", image=b"\x89PNG")
    assert answer.frame_key == "4h"
    assert answer.debug["frame_source"] == "مقروء من الصورة"


def test_written_symbol_beats_the_screenshot(offline, monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(analyst, "vision_read", lambda image: vision.ChartRead(
        is_chart=True, symbol="AAPL", frame_key="1d"))
    answer = analyst.analyze("حلل $TSLA يومي", image=b"\x89PNG")
    assert answer.symbol == "TSLA"
    assert answer.debug["symbol_source"] == "نص الرسالة"


def test_default_frame_when_neither_says_one(offline):
    answer = analyst.analyze("حلل TSLA")
    assert answer.frame_key == frames.get(config.DEFAULT_FRAME).key
    assert answer.debug["frame_source"] == "افتراضي"


def test_unknown_symbol_asks_for_one(offline):
    answer = analyst.analyze("حلل هذا الشارت")
    assert not answer.ok
    assert "ما عرفت الرمز" in answer.text


def test_non_chart_photo_is_dropped_silently(offline, monkeypatch):
    """A meme or a random screenshot in a group must not get a reply."""
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(analyst, "vision_read", lambda image: vision.ChartRead(is_chart=False))
    answer = analyst.analyze("شوفوا هذي", image=b"\x89PNG", addressed=False)
    assert answer.silent is True
    assert answer.text == ""


def test_non_chart_photo_still_answers_when_asked_directly(offline, monkeypatch):
    """If the user says "حلل", answer even if the vision read disagrees."""
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(analyst, "vision_read", lambda image: vision.ChartRead(is_chart=False))
    answer = analyst.analyze("حلل TSLA يومي", image=b"\x89PNG", addressed=True)
    assert answer.silent is False
    assert answer.ok is True


def test_chart_photo_with_free_caption_is_analysed(offline, monkeypatch):
    """Any wording works: the caption is passed through as the question."""
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(analyst, "vision_read", lambda image: vision.ChartRead(
        is_chart=True, symbol="TSLA", frame_key="15m"))
    answer = analyst.analyze("ادخل ولا أنتظر؟", image=b"\x89PNG", addressed=False)
    assert answer.ok is True and answer.silent is False
    assert answer.symbol == "TSLA" and answer.frame_key == "15m"


def test_photo_with_a_named_symbol_is_analysed_without_vision(offline, monkeypatch):
    """No Groq key: a caption naming the symbol is enough to act on the photo."""
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    answer = analyst.analyze("وش رايك في TSLA يومي", image=b"\x89PNG", addressed=False)
    assert answer.ok is True and answer.symbol == "TSLA"


def test_photo_without_vision_or_symbol_stays_silent(offline, monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    answer = analyst.analyze("وش رايكم", image=b"\x89PNG", addressed=False)
    assert answer.silent is True


def test_saudi_request_says_it_is_out_of_market(offline):
    """US-only deployment: name the reason instead of "I found no symbol"."""
    answer = analyst.analyze("حلل أرامكو يومي")
    assert not answer.ok
    assert "خارج السوق" in answer.text
    assert answer.symbol == "2222.SR"


def test_session_state_reaches_the_answer(offline):
    answer = analyst.analyze("حلل TSLA يومي")
    assert answer.ok
    assert "حالة السوق" in answer.text


def test_missing_data_is_reported(offline, monkeypatch):
    monkeypatch.setattr(analyst.market, "load", lambda *a, **k: None)
    answer = analyst.analyze("حلل TSLA يومي")
    assert not answer.ok
    assert "ما توفرت بيانات" in answer.text


def test_groq_failure_falls_back_to_the_template(offline, monkeypatch):
    from analyst_agent import groq_client

    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(analyst, "vision_read", lambda image: vision.ChartRead())
    def boom(*a, **k):
        raise groq_client.GroqError("rate limited")
    monkeypatch.setattr(analyst.groq_client, "chat", boom)
    monkeypatch.setattr(analyst.groq_client, "resolve_model", lambda kind: "m")
    answer = analyst.analyze("حلل TSLA يومي")
    assert answer.ok
    assert answer.used_model is None
    assert "الخلاصة" in answer.text


def test_groq_text_is_used_when_it_answers(offline, monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    long_reply = "🎯 الخلاصة: صاعد" + " تفاصيل" * 40
    monkeypatch.setattr(analyst.groq_client, "chat", lambda *a, **k: long_reply)
    monkeypatch.setattr(analyst.groq_client, "resolve_model", lambda kind: "model-x")
    answer = analyst.analyze("حلل TSLA يومي")
    assert answer.text.startswith("🎯 الخلاصة: صاعد")
    assert answer.used_model == "model-x"


def test_payload_carries_the_frame_and_verdict(offline, monkeypatch):
    captured = {}
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(analyst.groq_client, "resolve_model", lambda kind: "m")

    def capture(messages, **kwargs):
        captured["payload"] = messages[-1]["content"]
        return "🎯 الخلاصة: صاعد" + " تفاصيل" * 40

    monkeypatch.setattr(analyst.groq_client, "chat", capture)
    analyst.analyze("حلل TSLA فريم 4 ساعات وش الهدف")
    blob = captured["payload"]
    assert "وش الهدف" in blob                      # the question is passed through
    body = json.loads(blob[blob.index("{"):])
    assert body["requested_frame"] == "4 ساعات"
    assert body["verdict"]["direction"]
    assert body["technicals"]["frame"] == "4h"


@pytest.mark.parametrize("caption,expected", [
    ("حلل لي TSLA فريم 15 دقيقة", "لي TSLA فريم 15 دقيقة"),
    ("تحليل", None),
    ("@bot حلل", None),
    (None, None),
])
def test_clean_question(caption, expected):
    assert analyst.clean_question(caption) == expected


def test_fallback_template_mentions_every_section():
    from analyst_agent import indicators, verdict

    facts = indicators.analyze(make_df(trend=0.6, seed=71), "1h")
    facts["frame_label"] = "ساعة"
    call = verdict.decide(facts).to_dict()
    text = prompts.fallback_text(symbol="TSLA", frame_label="ساعة", facts=facts,
                                 verdict=call)
    for marker in ("الخلاصة", "القراءة الفنية", "المستويات", "ما يلغي السيناريو"):
        assert marker in text
    assert str(facts["price"]) in text
