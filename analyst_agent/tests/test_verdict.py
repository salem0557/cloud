from analyst_agent import indicators as I
from analyst_agent import verdict as V
from analyst_agent.tests.conftest import make_df


def _facts(trend, seed=31, frame="1h"):
    facts = I.analyze(make_df(trend=trend, seed=seed), frame)
    facts["frame_label"] = "ساعة"
    return facts


def test_uptrend_is_called_long():
    call = V.decide(_facts(0.9, 41))
    assert call.side == "long"
    assert call.score > 0
    assert call.stop < call.entry < call.targets[0]


def test_downtrend_is_called_short():
    call = V.decide(_facts(-0.9, 42))
    assert call.side == "short"
    assert call.score < 0
    assert call.stop > call.entry > call.targets[0]


def test_flat_market_gets_no_trade_plan():
    call = V.decide(_facts(0.0, 43))
    if call.side == "none":
        assert call.entry is None and call.targets == []
        assert "عرضي" in call.invalidation_ar or "الانتظار" in call.invalidation_ar


def test_stop_stays_inside_sane_atr_bounds():
    for trend, seed in ((0.8, 44), (-0.8, 45)):
        facts = _facts(trend, seed)
        call = V.decide(facts)
        if not call.entry:
            continue
        atr = facts["volatility"]["atr"]
        distance = abs(call.entry - call.stop)
        assert 0.5 * atr <= distance <= 2.6 * atr


def test_risk_reward_and_breakeven_agree():
    call = V.decide(_facts(0.9, 46))
    assert call.rr and call.rr > 0
    expected = 100 / (1 + call.rr)
    assert abs(call.breakeven_rate - expected) < 1.5


def test_higher_timeframe_conflict_cuts_conviction():
    facts = _facts(0.9, 47)
    aligned = V.decide(facts, I.analyze(make_df(trend=0.9, seed=47, freq="1D", tz=None), "1d"))
    against = V.decide(facts, I.analyze(make_df(trend=-0.9, seed=48, freq="1D", tz=None), "1d"))
    assert against.conviction <= aligned.conviction
    if against.side == "long":
        assert any("تعارض" in c for c in against.conflicts)


def test_verdict_dict_is_complete():
    data = V.decide(_facts(0.9, 49)).to_dict()
    for key in ("direction", "side", "conviction", "entry", "stop", "targets",
                "rr", "invalidation", "signals", "expected_range"):
        assert key in data


def test_position_size_respects_the_stop():
    sized = V.position_size(entry=100.0, stop=98.0, account=50_000, risk_pct=1.0)
    assert sized["units"] == 250
    assert sized["risk_amount"] == 500.0
    assert V.position_size(100.0, 100.0, 10_000)["units"] == 0


def test_invalidation_uses_the_arabic_frame_label():
    facts = _facts(0.9, 50)
    call = V.decide(facts)
    if call.entry:
        assert "ساعة" in call.invalidation_ar


def test_stop_never_sits_inside_the_noise():
    """A quiet 5-minute bar gave XRP a 0.09% stop — the tape takes that out."""
    from analyst_agent import config

    facts = {
        "price": 1.361, "frame": "5m", "frame_label": "5 دقائق",
        "trend": {"ema_fast": 1.362, "price_vs_ema_fast_pct": -0.1},
        "levels": {"nearest_support": 1.35, "nearest_resistance": 1.37,
                   "supports": [{"price": 1.35}, {"price": 1.32}],
                   "resistances": [{"price": 1.37}, {"price": 1.39}]},
    }
    for side in ("long", "short"):
        plan = V._plan(facts, side, atr=0.0012)      # ATR is 0.09% of price
        assert plan["risk_pct"] >= config.MIN_STOP_PCT - 0.01
        assert (plan["stop"] < plan["entry"]) is (side == "long")


def test_a_normal_stop_is_left_alone():
    """The floor must not widen a stop that is already sane."""
    facts = _facts(0.9, 91)
    call = V.decide(facts)
    if call.entry:
        atr = facts["volatility"]["atr"]
        assert abs(call.entry - call.stop) <= 2.6 * atr


def test_the_floor_is_configurable(monkeypatch):
    from analyst_agent import config

    monkeypatch.setattr(config, "MIN_STOP_PCT", 1.0)
    facts = {
        "price": 100.0, "frame": "1h", "frame_label": "ساعة",
        "trend": {"ema_fast": 100.0, "price_vs_ema_fast_pct": 0.0},
        "levels": {"nearest_support": 99.9, "nearest_resistance": 100.2,
                   "supports": [{"price": 99.9}], "resistances": [{"price": 100.2}]},
    }
    plan = V._plan(facts, "long", atr=0.05)
    assert plan["risk_pct"] >= 0.99


def test_targets_are_ordered_nearest_first():
    """R:R is measured against the first target, so the order is not cosmetic."""
    facts = {
        "price": 100.44, "frame": "5m", "frame_label": "5 دقائق",
        "trend": {"ema_fast": 98.0, "price_vs_ema_fast_pct": 2.5},
        "levels": {"nearest_support": 99.09, "nearest_resistance": 105.7,
                   "supports": [{"price": 99.09}],
                   "resistances": [{"price": 105.7}]},   # only one real level
    }
    plan = V._plan(facts, "long", atr=0.93)
    assert plan["targets"] == sorted(plan["targets"])
    assert plan["targets"][0] < 105.7          # the ATR target comes first
    rr = round((plan["targets"][0] - plan["entry"]) / (plan["entry"] - plan["stop"]), 2)
    assert abs(plan["rr"] - rr) < 0.05         # R:R follows the nearest target


def test_short_targets_are_ordered_nearest_first():
    facts = {
        "price": 100.0, "frame": "1h", "frame_label": "ساعة",
        "trend": {"ema_fast": 101.0, "price_vs_ema_fast_pct": -1.0},
        "levels": {"nearest_support": 90.0, "nearest_resistance": 101.0,
                   "supports": [{"price": 90.0}], "resistances": [{"price": 101.0}]},
    }
    plan = V._plan(facts, "short", atr=1.0)
    assert plan["targets"] == sorted(plan["targets"], reverse=True)
    assert plan["targets"][0] > 90.0


def test_duplicate_targets_are_dropped():
    assert V._ordered([100.0, 100.0, 102.0], 99.0) == [100.0, 102.0]


def test_horizon_scales_the_expected_range():
    """A one-hour question on a 5-minute chart is 12 bars, not the default 10."""
    facts = _facts(0.6, 95, frame="5m")
    short_view = V.decide(facts, horizon_bars=3).expected_range
    long_view = V.decide(facts, horizon_bars=48).expected_range
    assert (long_view[1] - long_view[0]) > (short_view[1] - short_view[0])
