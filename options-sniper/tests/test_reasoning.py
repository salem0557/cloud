"""The chain Salem asked for: stock breaks a level -> strike -> buy.

    "السهم الفلاني كسر المقاومة وسيصل السعر كذا، فإن هذا معناه العقد صاحب
     السترايك كذا سيرتفع، اشتر الآن."

Every link has to come from a number already computed. A link whose inputs are
missing must be absent and named, not filled in — that is the whole difference
between this and the previous system, which printed a win rate derived from its
own option score.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import reasoning


def payload(**over):
    p = {"ticker": "NVDA", "direction": "call", "spot": 182.90,
         "technical": {"level": 182.40, "close": 182.90, "atr": 1.80,
                       "target": 185.10, "stop": 180.90, "volume_ratio": 2.3,
                       "closed_beyond": True, "expected_move": 2.20},
         "tiers": [{"option_symbol": "NVDA260904C00185000", "strike": 185.0,
                    "type": "call", "ask": 0.95, "cost": 95.0, "delta": 0.42,
                    "gamma": 0.08, "expected_profit_pct": 58.0}]}
    p.update(over)
    return p


def steps(built):
    return [l["step"] for l in built["links"]]


# ── The chain runs one way: stock, then contract ────────────────
def test_the_chain_starts_at_the_stock_and_ends_at_the_contract():
    assert steps(reasoning.chain(payload())) == [
        "break", "target", "invalidation", "strike", "greeks", "contract"]


def test_a_call_reads_resistance_and_a_put_reads_support():
    up = reasoning.as_text(reasoning.chain(payload()))
    assert "كسر المقاومة" in up and "أغلق فوقها" in up
    down = reasoning.chain(payload(
        direction="put", spot=101.20,
        technical={"level": 101.80, "close": 101.20, "atr": 1.10,
                   "target": 99.55, "stop": 102.30, "volume_ratio": 1.9,
                   "closed_beyond": True, "expected_move": 1.65},
        tiers=[{"option_symbol": "L", "strike": 100.0, "type": "put",
                "ask": 0.72, "cost": 72.0, "delta": -0.40,
                "expected_profit_pct": 44.0}]))
    text = reasoning.as_text(down)
    assert "كسر الدعم" in text and "أغلق تحتها" in text
    assert "الفكرة تسقط إذا رجع السعر فوق 102.3" in text


def test_the_strike_link_says_what_the_move_does_to_it():
    """This is the link Salem named: the target moves the strike from out of
    the money to in it, which is why the contract reprices."""
    text = reasoning.as_text(reasoning.chain(payload()))
    assert "عند 185.1 يصبح الإضراب 185 من خارج المال إلى داخل المال" in text


def test_the_contract_move_comes_from_delta_not_from_a_guess():
    text = reasoning.as_text(reasoning.chain(payload()))
    assert "دلتا العقد 0.42" in text
    assert "0.92$" in text                      # 0.42 x 2.20, stated, not rounded up
    assert "تقدير بالدلتا وليس وعداً" in text


# ── What it refuses to say ──────────────────────────────────────
def test_no_break_means_no_chain_at_all():
    """Without a measured level there is no first cause, so there is nothing
    to reason from — and the run says that instead of starting at the contract."""
    built = reasoning.chain(payload(technical={}))
    assert built["links"] == []
    assert any("لا يوجد كسر" in g for g in built["gaps"])


def test_a_missing_delta_breaks_the_link_and_says_so():
    """The previous system would have printed a win rate anyway. Here the
    contract link is absent and the gap is named."""
    built = reasoning.chain(payload(tiers=[
        {"option_symbol": "X", "strike": 185.0, "ask": 0.95, "cost": 95.0,
         "delta": None, "expected_profit_pct": None}]))
    assert "greeks" not in steps(built)
    assert any("الدلتا غير متاحة" in g for g in built["gaps"])


def test_no_affordable_contract_stops_the_chain_at_the_stock():
    built = reasoning.chain(payload(tiers=[{"tier": "🔴 <50$",
                                            "option_symbol": None}]))
    assert steps(built) == ["break", "target", "invalidation"]
    assert any("لا عقد ضمن الميزانية" in g for g in built["gaps"])


def test_an_unclosed_bar_is_not_described_as_a_close():
    tech = dict(payload()["technical"], closed_beyond=False)
    text = reasoning.as_text(reasoning.chain(payload(technical=tech)))
    assert "لم تُغلق الشمعة بعد خلفها" in text


def test_every_number_in_the_chain_is_carried_alongside_the_words():
    """So a reader can check any sentence against the figure it came from."""
    for link in reasoning.chain(payload())["links"]:
        assert link["numbers"]
        assert all(v is not None for v in link["numbers"].values())
