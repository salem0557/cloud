"""The analyst layer: grounded in measured base rates, and never load-bearing."""
import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import analyst
import backtest
import config as C


BOOK = {
    "overall": {"count": 400, "hit_rate": 52.0},
    "by_setup": {
        "call|3x+|0.5-1atr|closed|open": {"count": 60, "hit_rate": 64.0},
        "call|1.5-2x|0-0.5atr|wick|close": {"count": 25, "hit_rate": 38.0},
    },
}


# ── Base rates come from measurement, never from the model ──────
def test_exact_setup_rate_is_used():
    r = analyst.base_rate_for("call|3x+|0.5-1atr|closed|open", BOOK)
    assert r["available"] and r["hit_rate"] == 64.0 and r["count"] == 60


def test_unknown_setup_falls_back_to_overall_and_says_so():
    r = analyst.base_rate_for("put|3x+|1atr+|closed|midday", BOOK)
    assert r["available"] and r["hit_rate"] == 52.0
    assert "عينة كافية" in r["scope"]        # the fallback is disclosed


def test_missing_backtest_is_reported_not_faked():
    """With no history the analyst must know its read is unanchored rather
    than be handed a number to rationalise."""
    r = analyst.base_rate_for("anything", None)
    assert r["available"] is False and r["reason"]


def test_empty_backtest_is_reported():
    r = analyst.base_rate_for("anything", {"overall": {"count": 0}, "by_setup": {}})
    assert r["available"] is False


# ── The brief carries what the analyst is allowed to reason over ─
def _payload():
    return {
        "ticker": "NVDA", "score": 88.0, "raw_score": 92.0,
        "score_breakdown": {"flow": 29.3, "technical": 23.7,
                            "catalyst": 20.0, "liquidity": 13.7},
        "risk": {"penalty": 4.0, "flags": ["السوق ينزل 1.2%"]},
        "direction": "call", "spot": 100.85,
        "technical": {"volume_ratio": 3.4, "break_distance_atr": 0.7,
                      "closed_beyond": True, "bar_time": "2026-09-08T14:45:00Z",
                      "level": 100.6, "target": 102.37, "stop": 99.42, "atr": 0.67},
        "flow_reason": "شراء كول", "news": [], "tiers": [], "time_riyadh": "18:47",
    }


def test_brief_includes_the_measured_rate():
    b = analyst._brief(_payload(), BOOK)
    assert b["base_rate"]["available"]
    assert b["base_rate_min_sample"] == C.BASE_RATE_MIN_SAMPLE


def test_brief_carries_both_scores_and_the_risk_flags():
    b = analyst._brief(_payload(), BOOK)
    assert b["computed_score"] == 92.0 and b["score_after_risk"] == 88.0
    assert b["risk_flags"] == ["السوق ينزل 1.2%"]


def test_setup_key_is_stable_between_backtest_and_analyst():
    """The lookup only works if both sides bucket a setup identically."""
    t = _payload()["technical"]
    assert backtest.setup_key(t, "call") == "call|3x+|0.5-1atr|closed|open"


# ── The layer is never load-bearing ─────────────────────────────
def test_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(C, "USE_ANALYST", False)
    assert analyst.review(_payload()) is None


def test_api_failure_returns_none_not_a_block(monkeypatch):
    """An unreachable analyst must let the alert through on the arithmetic —
    a network error is not a reason to suppress a qualifying setup."""
    monkeypatch.setattr(C, "USE_ANALYST", True)
    monkeypatch.setattr(analyst, "load_book", lambda: BOOK)

    import builtins
    real_import = builtins.__import__

    def no_anthropic(name, *a, **k):
        if name == "anthropic":
            raise ImportError("not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_anthropic)
    assert analyst.review(_payload()) is None


def test_non_json_reply_is_ignored(monkeypatch):
    monkeypatch.setattr(C, "USE_ANALYST", True)
    monkeypatch.setattr(analyst, "load_book", lambda: BOOK)

    class Block:
        type, text = "text", "I think this looks pretty good honestly"

    class Resp:
        content = [Block()]

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kw):
                return Resp()

    import types
    fake = types.SimpleNamespace(Anthropic=lambda *a, **k: FakeClient())
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    assert analyst.review(_payload()) is None


def test_valid_reply_is_returned_with_its_base_rate(monkeypatch):
    monkeypatch.setattr(C, "USE_ANALYST", True)
    monkeypatch.setattr(analyst, "load_book", lambda: BOOK)

    reply = json.dumps({"verdict": "TAKE", "conviction": "عالية",
                        "vs_base_rate": "أعلى", "tier": "🟡",
                        "reading": "التدفق مشترى والكسر مؤكد.",
                        "concerns": []}, ensure_ascii=False)

    class Block:
        type, text = "text", reply

    class Resp:
        content = [Block()]

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kw):
                return Resp()

    import types
    monkeypatch.setitem(sys.modules, "anthropic",
                        types.SimpleNamespace(Anthropic=lambda *a, **k: FakeClient()))
    note = analyst.review(_payload())
    assert note["verdict"] == "TAKE"
    assert note["base_rate"]["hit_rate"] == 64.0     # measured, not from the model
    assert note["model"] == C.ANALYST_MODEL
