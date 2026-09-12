import pytest

from analyst_agent import frames


@pytest.mark.parametrize("text,expected", [
    ("حلل لي السهم 15 دقيقة", "15m"),
    ("M15", "15m"),
    ("m5", "5m"),
    ("تحليل على فريم 4 ساعات", "4h"),
    ("240", "4h"),
    ("H4", "4h"),
    ("ربع ساعة", "15m"),
    ("نص ساعة", "30m"),
    ("اليومي", "1d"),
    ("daily chart please", "1d"),
    ("D1", "1d"),
    ("اسبوعي", "1wk"),
    ("weekly", "1wk"),
    ("شهري", "1mo"),
    ("1mo", "1mo"),
    ("1m", "1m"),
    ("ساعتين", "2h"),
    ("١٥ دقيقة", "15m"),          # Arabic-Indic digits
    ("60m", "1h"),
])
def test_parse(text, expected):
    parsed = frames.parse(text)
    assert parsed is not None, text
    assert parsed.key == expected


@pytest.mark.parametrize("text", ["حلل", "مرحبا", "TSLA", ""])
def test_parse_finds_nothing(text):
    assert frames.parse(text) is None


def test_unknown_falls_back_to_daily():
    assert frames.get("nonsense").key == "1d"


def test_seven_minutes_rounds_down_to_a_supported_frame():
    assert frames.parse("7 دقائق").key == "5m"


def test_every_frame_has_a_download_plan():
    for key, frame in frames.FRAMES.items():
        assert frame.interval and frame.period, key
        assert frame.minutes > 0
        assert frame.label_ar and frame.label_en
        if frame.context:
            assert frames.FRAMES[frame.context].minutes > frame.minutes


def test_context_is_one_step_higher():
    assert frames.context_frame(frames.get("15m")).key == "1h"
    assert frames.context_frame(frames.get("1d")).key == "1wk"
    assert frames.context_frame(frames.get("1mo")) is None


@pytest.mark.parametrize("label,expected", [
    ("D", "1d"), ("W", "1wk"), ("M", "1mo"), ("MN", "1mo"), ("H", "1h"),
    ("M15", "15m"), ("240", "4h"), ("1D", "1d"), ("4h", "4h"),
])
def test_parse_label_accepts_terse_chart_fields(label, expected):
    """The timeframe printed in a chart corner is often a single letter."""
    assert frames.parse_label(label).key == expected


@pytest.mark.parametrize("text", ["حلل MO يومي", "W هذا السهم قوي"])
def test_bare_letters_in_a_sentence_are_not_frames(text):
    """"W" and "MO" are real tickers: free text must not read them as frames."""
    parsed = frames.parse(text)
    assert parsed is None or parsed.key == "1d"
