import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import uw


def test_occ_parsing():
    p = uw.parse_occ("AAPL240202P00185000")
    assert p == {"ticker": "AAPL", "expiry": "2024-02-02", "type": "put", "strike": 185.0}
    assert uw.parse_occ("UBER260911C00076000")["strike"] == 76.0
    assert uw.parse_occ("garbage") is None


def test_uw_string_numbers_are_coerced():
    """UW sends '4.05' not 4.05 — the whole pipeline breaks without this."""
    assert uw._num("4.05") == 4.05
    assert uw._num(None) == 0.0
    assert uw._num("") == 0.0
    assert uw._num("not-a-number") == 0.0


def test_contract_normalisation_maps_uw_field_names():
    row = {"option_symbol": "AAPL231020C00185000", "strike": 185, "expires": "2023-10-20",
           "option_type": "call", "nbbo_bid": "4.35", "nbbo_ask": "4.45",
           "delta": "0.4573", "open_interest": 1200, "volume": 842}
    c = uw._normalise_contract(row)
    assert c["bid"] == 4.35 and c["ask"] == 4.45      # nbbo_bid/ask -> bid/ask
    assert c["type"] == "call"                        # option_type -> type
    assert c["expiry"] == "2023-10-20"                # expires -> expiry
    assert c["delta"] == 0.4573


def test_normalisation_falls_back_to_the_occ_symbol():
    c = uw._normalise_contract({"option_symbol": "UBER260911C00076000"})
    assert c["strike"] == 76.0 and c["type"] == "call" and c["expiry"] == "2026-09-11"


# ── Placeholder guard ───────────────────────────────────────────
import importlib, os
import config as _config


def _reload(**env):
    for k in ("UW_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "FINVIZ_AUTH"):
        os.environ.pop(k, None)
    os.environ.update(env)
    return importlib.reload(_config)


def test_shipped_placeholders_are_treated_as_unset():
    """Railway offers to import .env.example verbatim; a placeholder must not
    read as a configured key, or the first UW call fails with a bare 401."""
    c = _reload(UW_API_KEY="ضع_مفتاح_unusual_whales_هنا",
                TELEGRAM_BOT_TOKEN="123456789:AAAA-your-token-here",
                TELEGRAM_CHAT_ID="ضع_رقم_الشات_هنا")
    assert c.UW_API_KEY == ""
    assert c.TELEGRAM_TOKEN == ""
    assert c.TELEGRAM_CHAT_ID == ""


def test_real_values_pass_through():
    c = _reload(UW_API_KEY="abc123realkey",
                TELEGRAM_BOT_TOKEN="8123456789:AAHrealtoken",
                TELEGRAM_CHAT_ID="987654321")
    assert c.UW_API_KEY == "abc123realkey"
    assert c.TELEGRAM_TOKEN == "8123456789:AAHrealtoken"
    assert c.TELEGRAM_CHAT_ID == "987654321"
    _reload()


# ── DTE window and delta requirement ────────────────────────────
import datetime
import config as C2


def _c(days, delta=0.5):
    d = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
    return {"expiry": d, "delta": delta}


def test_far_dated_leaps_are_excluded():
    """AAPL's chain came back with 3,294 contracts running to 2028. A 2028
    LEAP is not a candidate for a 15-minute breakout."""
    assert not uw._in_window(_c(900))
    assert not uw._in_window(_c(C2.MAX_DTE + 1))


def test_same_day_expiry_is_allowed():
    """Salem trades 0DTE; the window starts at 0, not 2."""
    assert C2.MIN_DTE == 0
    assert uw._in_window(_c(0))


def test_contracts_inside_the_window_pass():
    assert uw._in_window(_c(C2.MIN_DTE))
    assert uw._in_window(_c(C2.MAX_DTE))
    assert uw._in_window(_c(21))


def test_contracts_without_delta_are_dropped():
    """About half of a full UW chain has no greeks; without a delta the
    profit estimate can only read 0%."""
    assert not uw._in_window(_c(21, delta=0))
    assert not uw._in_window(_c(21, delta=None))


def test_unparseable_expiry_is_dropped():
    assert not uw._in_window({"expiry": "", "delta": 0.5})
    assert not uw._in_window({"expiry": "not-a-date", "delta": 0.5})
