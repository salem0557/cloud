import pytest

from analyst_agent import symbols


@pytest.mark.parametrize("text,expected", [
    ("حلل $TSLA يومي", "TSLA"),
    ("ارامكو 15 دقيقة", "2222.SR"),
    ("2222 اليومي", "2222.SR"),
    ("سابك اسبوعي", "2010.SR"),
    ("tasi", "^TASI.SR"),
    ("BTC 4h", "BTC-USD"),
    ("حلل لي الذهب", "GC=F"),
    ("eurusd 1h", "EURUSD=X"),
    ("2010.SR", "2010.SR"),
    ("تسلا", "TSLA"),
])
def test_best_candidate(text, expected):
    found = symbols.resolve(text)
    assert found, text
    assert found[0].symbol == expected


def test_timeframe_number_is_not_a_ticker():
    assert not any(c.symbol.startswith("240") for c in symbols.resolve("حلل السهم على فريم 240"))


def test_no_symbol_in_a_bare_request():
    assert symbols.resolve("تحليل") == []


@pytest.mark.parametrize("raw,expected", [
    ("TADAWUL:2222", "2222.SR"),
    ("BINANCE:BTCUSDT", "BTC-USD"),
    ("XAUUSD", "GC=F"),
    ("2222", "2222.SR"),
    ("AAPL", "AAPL"),
    ("nasdaq", "^IXIC"),
    ("", None),
    ("unknown-!!", None),
])
def test_normalize_symbol(raw, expected):
    assert symbols.normalize_symbol(raw) == expected


def test_saudi_helpers():
    assert symbols.is_saudi("2222.SR")
    assert not symbols.is_saudi("AAPL")
    assert symbols.currency_of("2222.SR") == "ريال"
