import pytest

from analyst_agent import config, symbols


@pytest.mark.parametrize("text,expected", [
    ("حلل $TSLA يومي", "TSLA"),
    ("تسلا", "TSLA"),
    ("انفيديا 15 دقيقة", "NVDA"),
    ("NVDA", "NVDA"),
    ("spy اسبوعي", "SPY"),
    ("نازداك", "^IXIC"),
    ("BTC 4h", "BTC-USD"),
    ("حلل لي الذهب", "GC=F"),
    ("eurusd 1h", "EURUSD=X"),
])
def test_best_candidate(text, expected):
    found = symbols.resolve(text)
    assert found, text
    assert found[0].symbol == expected


def test_us_only_ignores_saudi_symbols():
    """The default deployment answers for the US market only."""
    assert symbols.resolve("ارامكو 15 دقيقة") == []
    assert symbols.resolve("2222 اليومي") == []
    assert symbols.resolve("tasi") == []
    assert symbols.normalize_symbol("TADAWUL:2222") is None
    assert symbols.normalize_symbol("2222") is None


def test_saudi_symbols_come_back_when_us_only_is_off(monkeypatch):
    monkeypatch.setattr(config, "US_ONLY", False)
    assert symbols.resolve("ارامكو 15 دقيقة")[0].symbol == "2222.SR"
    assert symbols.resolve("2222 اليومي")[0].symbol == "2222.SR"
    assert symbols.resolve("سابك اسبوعي")[0].symbol == "2010.SR"
    assert symbols.resolve("tasi")[0].symbol == "^TASI.SR"
    assert symbols.normalize_symbol("TADAWUL:2222") == "2222.SR"


def test_all_markets_lookup_sees_what_the_deployment_skips():
    """Used only to answer "that symbol is outside my market"."""
    assert symbols.resolve("ارامكو", all_markets=True)[0].symbol == "2222.SR"
    assert symbols.resolve("ارامكو") == []


def test_non_equity_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_NON_EQUITY", False)
    assert symbols.resolve("BTC 4h") == [] or all(
        c.symbol != "BTC-USD" for c in symbols.resolve("BTC 4h"))
    assert symbols.resolve("حلل لي الذهب") == []
    assert symbols.resolve("$TSLA")[0].symbol == "TSLA"


def test_timeframe_number_is_not_a_ticker():
    assert not any(c.symbol.startswith("240") for c in symbols.resolve("حلل السهم على فريم 240"))


def test_no_symbol_in_a_bare_request():
    assert symbols.resolve("تحليل") == []


@pytest.mark.parametrize("raw,expected", [
    ("BINANCE:BTCUSDT", "BTC-USD"),
    ("XAUUSD", "GC=F"),
    ("AAPL", "AAPL"),
    ("nasdaq", "^IXIC"),
    ("", None),
    ("unknown-!!", None),
])
def test_normalize_symbol(raw, expected):
    assert symbols.normalize_symbol(raw) == expected


def test_serves_reflects_the_configured_market():
    assert symbols.serves("AAPL")
    assert not symbols.serves("2222.SR")


def test_saudi_helpers():
    assert symbols.is_saudi("2222.SR")
    assert not symbols.is_saudi("AAPL")
    assert symbols.currency_of("2222.SR") == "ريال"


@pytest.mark.parametrize("text,expected", [
    ("btc 15m", "BTC-USD"),
    ("eth يومي", "ETH-USD"),
    ("سولانا 4 ساعات", "SOL-USD"),
    ("حلل شيبا", "SHIB-USD"),
    ("SUI", "SUI-USD"),
    ("ethusdt", "ETH-USD"),
    ("كريبتو", "BTC-USD"),
])
def test_crypto_is_covered(text, expected):
    found = symbols.resolve(text)
    assert found and found[0].symbol == expected


@pytest.mark.parametrize("text,expected", [
    ("LINKUSD كم سيصل سعرها؟", "LINK-USD"),
    ("BTCUSDT", "BTC-USD"),
    ("ethusd يومي", "ETH-USD"),
    ("SOL/USD 15m", "SOL-USD"),
    ("DOGEUSDT", "DOGE-USD"),
])
def test_exchange_style_pairs(text, expected):
    """TradingView and Binance write pairs as one word."""
    found = symbols.resolve(text)
    assert found and found[0].symbol == expected


def test_currency_pairs_are_not_read_as_coins():
    assert symbols.resolve("eurusd 1h")[0].symbol == "EURUSD=X"
    assert symbols.resolve("usdjpy")[0].symbol == "USDJPY=X"


def test_aptos_resolves_to_the_real_coin():
    """APT-USD on Yahoo is a dead $0.0002 token; Aptos is APT21794-USD."""
    assert symbols.resolve("APTUSDT")[0].symbol == "APT21794-USD"
    assert symbols.resolve("aptos 4h")[0].symbol == "APT21794-USD"


def test_crypto_lookup_picks_the_matching_coin(monkeypatch):
    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"quotes": [
                {"symbol": "APTUSD=X", "quoteType": "CURRENCY"},
                {"symbol": "APT21794-USD", "quoteType": "CRYPTOCURRENCY"},
                {"symbol": "APT-USD", "quoteType": "CRYPTOCURRENCY"},
            ]}

    monkeypatch.setattr(symbols, "_crypto_cache", {})
    monkeypatch.setattr(symbols.requests, "get", lambda *a, **k: Resp())
    assert symbols.lookup_crypto("APT") == "APT21794-USD"


def test_crypto_lookup_is_cached(monkeypatch):
    calls = {"n": 0}

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"quotes": [{"symbol": "XYZ9-USD", "quoteType": "CRYPTOCURRENCY"}]}

    def counting_get(*a, **k):
        calls["n"] += 1
        return Resp()

    monkeypatch.setattr(symbols, "_crypto_cache", {})
    monkeypatch.setattr(symbols.requests, "get", counting_get)
    assert symbols.lookup_crypto("XYZ") == "XYZ9-USD"
    assert symbols.lookup_crypto("XYZ") == "XYZ9-USD"
    assert calls["n"] == 1


def test_a_failed_lookup_is_not_fatal(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(symbols, "_crypto_cache", {})
    monkeypatch.setattr(symbols.requests, "get", boom)
    assert symbols.lookup_crypto("XYZ") is None


def test_an_unknown_pair_tries_the_lookup_then_the_plain_form(monkeypatch):
    monkeypatch.setattr(symbols, "_crypto_cache", {})
    monkeypatch.setattr(symbols, "lookup_crypto", lambda base: "WIF1234-USD")
    found = [c.symbol for c in symbols.resolve("WIFUSDT")]
    assert found[0] == "WIF1234-USD"
    assert "WIF-USD" in found          # kept as the fallback candidate
