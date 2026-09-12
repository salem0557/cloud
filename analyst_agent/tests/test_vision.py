import json

import pytest

from analyst_agent import groq_client, vision


def _reply(**overrides):
    payload = {
        "is_chart": True, "symbol": "TADAWUL:2222", "company_name": "Saudi Aramco",
        "exchange": "TADAWUL", "timeframe": "1D", "last_price": "27.85",
        "currency": "SAR", "indicators_visible": ["RSI", "Volume"],
        "user_drawings": ["support zone"], "drawn_prices": ["27.00"],
        "chart_style": "candles", "visible_date_range": "Jan-Sep 2026",
        "notes": "log scale",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_reads_symbol_and_frame(monkeypatch):
    monkeypatch.setattr(vision.groq_client, "vision_chat", lambda *a, **k: _reply())
    read = vision.read_chart(b"img")
    assert read.is_chart
    assert read.symbol == "2222.SR"
    assert read.frame_key == "1d"
    assert read.last_price == 27.85
    assert "RSI" in read.indicators
    assert read.drawings == ["support zone"]


def test_company_name_saves_a_missing_ticker(monkeypatch):
    monkeypatch.setattr(vision.groq_client, "vision_chat",
                        lambda *a, **k: _reply(symbol=None, company_name="تسلا"))
    assert vision.read_chart(b"img").symbol == "TSLA"


def test_binance_pair_becomes_a_yahoo_symbol(monkeypatch):
    monkeypatch.setattr(vision.groq_client, "vision_chat",
                        lambda *a, **k: _reply(symbol="BINANCE:BTCUSDT", timeframe="4h"))
    read = vision.read_chart(b"img")
    assert read.symbol == "BTC-USD"
    assert read.frame_key == "4h"


def test_unreadable_reply_is_reported(monkeypatch):
    monkeypatch.setattr(vision.groq_client, "vision_chat", lambda *a, **k: "sorry, no idea")
    read = vision.read_chart(b"img")
    assert read.symbol is None
    assert read.error


def test_groq_error_is_reported(monkeypatch):
    def boom(*a, **k):
        raise groq_client.GroqError("no key")

    monkeypatch.setattr(vision.groq_client, "vision_chat", boom)
    read = vision.read_chart(b"img")
    assert read.error == "no key"
    assert read.symbol is None


def test_not_a_chart_is_respected(monkeypatch):
    monkeypatch.setattr(vision.groq_client, "vision_chat",
                        lambda *a, **k: _reply(is_chart=False, symbol=None,
                                               company_name=None, exchange=None))
    read = vision.read_chart(b"img")
    assert read.is_chart is False
    assert read.symbol is None


@pytest.mark.parametrize("value,expected", [
    ("1,234.50", 1234.5), ("$27.85", 27.85), (None, None), ("n/a", None), (12, 12.0),
])
def test_price_parsing(value, expected):
    assert vision._as_float(value) == expected
