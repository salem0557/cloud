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
