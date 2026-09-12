"""The automatic recommendations: what clears the bar, what is held back."""
import time

import pytest

from analyst_agent import config, indicators, market, verdict, watcher
from analyst_agent.market import MarketData
from analyst_agent.tests.conftest import make_df


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(config, "WATCH_SIDES", "long")
    monkeypatch.setattr(config, "WATCH_ONLY_WHEN_OPEN", False)
    monkeypatch.setattr(config, "WATCH_MAX_PER_RUN", 3)
    monkeypatch.setattr(config, "WATCH_MAX_PER_DAY", 10)
    monkeypatch.setattr(config, "WATCH_MIN_REL_VOLUME", 0.0)
    monkeypatch.setattr(config, "WATCH_COOLDOWN_HOURS", 12)
    monkeypatch.setattr(config, "ALERTS_CHAT", -100123)
    monkeypatch.setattr(config, "ALERTS_TOPIC", 1)


def _setup(trend=0.9, seed=41, frame="1h"):
    facts = indicators.analyze(make_df(trend=trend, seed=seed), frame)
    facts["frame_label"] = "ساعة"
    facts["session"] = {"is_open": True, "phase_ar": "مفتوح"}
    return facts, verdict.decide(facts).to_dict()


def test_a_strong_uptrend_clears_the_bar():
    facts, call = _setup()
    call.update(conviction=80, score=60.0, rr=2.0, side="long",
                entry=100.0, stop=98.0)
    ok, why = watcher.eligible(facts, call, {"posted": {}}, "NVDA")
    assert ok is True, why


@pytest.mark.parametrize("override,expected_reason", [
    ({"conviction": 40}, "conviction"),
    ({"score": 10.0}, "score"),
    ({"rr": 0.8}, "rr"),
    ({"side": "short"}, "side"),
    ({"entry": None}, "no trade plan"),
])
def test_each_condition_can_hold_a_setup_back(override, expected_reason):
    facts, call = _setup()
    call.update(conviction=80, score=60.0, rr=2.0, side="long", entry=100.0, stop=98.0)
    call.update(override)
    ok, why = watcher.eligible(facts, call, {"posted": {}}, "NVDA")
    assert ok is False
    assert expected_reason in why


def test_weak_adx_is_rejected():
    facts, call = _setup()
    facts["trend"]["adx"] = 9.0
    call.update(conviction=80, score=60.0, rr=2.0, side="long", entry=100.0, stop=98.0)
    ok, why = watcher.eligible(facts, call, {"posted": {}}, "NVDA")
    assert ok is False and "adx" in why


def test_thin_volume_is_rejected(monkeypatch):
    monkeypatch.setattr(config, "WATCH_MIN_REL_VOLUME", 0.9)
    facts, call = _setup()
    facts["volume"]["relative"] = 0.3
    call.update(conviction=80, score=60.0, rr=2.0, side="long", entry=100.0, stop=98.0)
    assert watcher.eligible(facts, call, {"posted": {}}, "NVDA")[0] is False


def test_closed_market_blocks_posting(monkeypatch):
    monkeypatch.setattr(config, "WATCH_ONLY_WHEN_OPEN", True)
    facts, call = _setup()
    facts["session"] = {"is_open": False, "phase_ar": "مغلق"}
    call.update(conviction=80, score=60.0, rr=2.0, side="long", entry=100.0, stop=98.0)
    ok, why = watcher.eligible(facts, call, {"posted": {}}, "NVDA")
    assert ok is False and "closed" in why


def test_cooldown_blocks_a_repeat_but_not_a_flip():
    facts, call = _setup()
    call.update(conviction=80, score=60.0, rr=2.0, side="long", entry=100.0, stop=98.0)
    state = {"posted": {"NVDA": {"ts": time.time(), "side": "long"}}}
    assert watcher.eligible(facts, call, state, "NVDA")[0] is False
    state["posted"]["NVDA"]["side"] = "short"      # direction flipped
    assert watcher.eligible(facts, call, state, "NVDA")[0] is True


def test_expired_cooldown_allows_a_repeat(monkeypatch):
    monkeypatch.setattr(config, "WATCH_COOLDOWN_HOURS", 2)
    facts, call = _setup()
    call.update(conviction=80, score=60.0, rr=2.0, side="long", entry=100.0, stop=98.0)
    state = {"posted": {"NVDA": {"ts": time.time() - 3 * 3600, "side": "long"}}}
    assert watcher.eligible(facts, call, state, "NVDA")[0] is True


def test_sides_allowed(monkeypatch):
    monkeypatch.setattr(config, "WATCH_SIDES", "both")
    assert watcher.sides_allowed() == {"long", "short"}
    monkeypatch.setattr(config, "WATCH_SIDES", "short")
    assert watcher.sides_allowed() == {"short"}
    monkeypatch.setattr(config, "WATCH_SIDES", "nonsense")
    assert watcher.sides_allowed() == {"long"}


def test_state_round_trip_and_daily_cap():
    alert = watcher.Alert("NVDA", "long", 80, "text", None, "1h")
    watcher.mark_posted([alert])
    state = watcher.load_state()
    assert state["posted"]["NVDA"]["side"] == "long"
    assert state["count"] == 1


def test_daily_cap_stops_a_scan(monkeypatch):
    monkeypatch.setattr(config, "WATCH_MAX_PER_DAY", 1)
    watcher.mark_posted([watcher.Alert("NVDA", "long", 80, "t", None, "1h")])
    assert watcher.scan(["TSLA"]) == []


def test_destination_falls_back_to_a_single_allowed_chat(monkeypatch):
    monkeypatch.setattr(config, "ALERTS_CHAT", 0)
    monkeypatch.setattr(config, "ALLOWED_CHATS", {-555})
    monkeypatch.setattr(config, "ALERTS_TOPIC", 0)
    assert watcher.destination() == (-555, None)
    monkeypatch.setattr(config, "ALLOWED_CHATS", {-555, -666})
    assert watcher.destination() is None


def test_enabled_needs_both_switch_and_destination(monkeypatch):
    assert watcher.enabled() is True
    monkeypatch.setattr(config, "WATCH_ENABLED", False)
    assert watcher.enabled() is False


def test_status_lists_the_conditions():
    text = watcher.status()
    assert "الشروط" in text and str(config.WATCH_MIN_CONVICTION) in text


@pytest.fixture
def offline_market(monkeypatch):
    """Two symbols: one in a strong uptrend, one going nowhere."""
    def fake_load(candidates, frame, with_meta=True):
        symbol = getattr(candidates[0], "symbol", candidates[0])
        # seed 41 is a clean uptrend, seed 13 goes nowhere (see test_verdict)
        trend, seed = (0.9, 41) if symbol == "STRONG" else (0.0, 13)
        data = MarketData(symbol=symbol, frame=frame,
                          df=make_df(trend=trend, seed=seed),
                          context_df=make_df(trend=trend, seed=seed, freq="1D", tz=None),
                          daily_df=make_df(trend=trend, seed=seed, n=400, freq="1D", tz=None))
        data.meta = {"symbol": symbol}
        return data

    monkeypatch.setattr(watcher.market, "load", fake_load)
    monkeypatch.setattr(watcher.market, "_meta", lambda symbol: {"name": f"{symbol} Inc"})
    monkeypatch.setattr(watcher.news_mod, "event_risk", lambda meta: {})
    monkeypatch.setattr(config, "WATCH_MIN_CONVICTION", 30)
    monkeypatch.setattr(config, "WATCH_MIN_SCORE", 20.0)
    monkeypatch.setattr(config, "WATCH_MIN_RR", 0.5)
    monkeypatch.setattr(config, "WATCH_MIN_ADX", 0.0)
    monkeypatch.setattr(config, "WATCH_MIN_REL_VOLUME", 0.0)
    monkeypatch.setattr(config, "WATCH_ONLY_WHEN_OPEN", False)


def test_scan_posts_the_qualifying_symbol_only(offline_market):
    alerts = watcher.scan(["STRONG", "FLAT"])
    assert [a.symbol for a in alerts] == ["STRONG"]
    alert = alerts[0]
    assert alert.side == "long"
    assert "توصية شراء" in alert.text
    assert "الستوب" in alert.text and "العائد/المخاطرة" in alert.text
    assert alert.chart_png and alert.chart_png[:4] == b"\x89PNG"


def test_scan_respects_the_per_run_cap(offline_market, monkeypatch):
    monkeypatch.setattr(config, "WATCH_MAX_PER_RUN", 1)
    assert len(watcher.scan(["STRONG", "STRONG"])) == 1


def test_scan_skips_a_symbol_with_earnings_in_two_days(offline_market, monkeypatch):
    monkeypatch.setattr(watcher.news_mod, "event_risk",
                        lambda meta: {"days_to_earnings": 2, "warning": "قريبة"})
    assert watcher.scan(["STRONG"]) == []


def test_scan_survives_a_broken_symbol(offline_market, monkeypatch):
    def explode(candidates, frame, with_meta=True):
        raise RuntimeError("network down")

    monkeypatch.setattr(watcher.market, "load", explode)
    assert watcher.scan(["STRONG"]) == []


def test_alert_text_has_no_leftover_internals(offline_market):
    alert = watcher.scan(["STRONG"])[0]
    assert "_df" not in alert.text and "_symbol" not in alert.text
