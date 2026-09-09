"""Three defects found by reading the live path end to end, 2026-09-09.

None of them raised, none of them logged, and all 627 tests passed with all
three present. They are the same shape as everything else this project has
shipped: two code paths that were supposed to agree, quietly disagreeing.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config as C
import monitor
import scanner
import technical


# ── 1. The paper book recorded tickers, not trades ──────────────
def _run_watchlist_scan(monkeypatch, tech, score, tmp):
    """One WATCHLIST_ONLY scan over one name. -> (sent, paper positions)."""
    sent, recorded = [], []
    cand = {"ticker": "MSFT", "score": score, "direction": "call",
            "spot": 500.0, "near_miss": False, "technical": tech,
            "score_breakdown": {"flow": 20.0, "technical": 0.0,
                                "catalyst": 20.0, "liquidity": 10.0}}
    monkeypatch.setattr(C, "WATCHLIST_ONLY", True)
    monkeypatch.setattr(C, "WATCHLIST", ["MSFT"])
    monkeypatch.setattr(C, "SHORTLIST_FILE", pathlib.Path(tmp))
    monkeypatch.setattr(C, "MIN_TICKER_PREMIUM", 0)
    monkeypatch.setattr(scanner, "send", lambda m: sent.append(m) or 1)
    monkeypatch.setattr(scanner.paper, "record",
                        lambda p, tier=None: recorded.append(p) or {})
    monkeypatch.setattr(scanner.state, "record_alert", lambda t, **kw: True)
    monkeypatch.setattr(scanner.market, "is_open", lambda *a: True)
    monkeypatch.setattr(scanner.state, "capacity_left", lambda: 30)
    monkeypatch.setattr(scanner.state, "read", lambda: {"alerted_tickers": []})
    monkeypatch.setattr(scanner.uw, "ticker_flow_alerts", lambda *a, **k: [])
    monkeypatch.setattr(scanner.uw, "spent", lambda: "")
    monkeypatch.setattr(scanner, "evaluate", lambda t, f, d=False: cand)
    monkeypatch.setattr(scanner, "to_payload",
                        lambda c: {"ticker": c["ticker"], "score": c["score"],
                                   "direction": c["direction"],
                                   "spot": c["spot"],
                                   "technical": c["technical"], "tiers": []})
    monkeypatch.setattr(scanner, "still_holding", lambda c: True)
    monkeypatch.setattr(scanner, "compose", lambda kind, p: "alert text")
    monkeypatch.setattr(scanner.journal, "log_alert", lambda p, kind=None: None)
    monkeypatch.setattr(scanner.mine, "remember_alert", lambda mid, p: None)
    scanner.main()
    return sent, recorded


def test_a_name_with_no_setup_at_all_never_reaches_the_paper_book(
        monkeypatch, tmp_path):
    """Under WATCHLIST_ONLY the alert gate is +inf for anything that is not a
    signal, so `score < gate` is true for EVERY name on the list. A ticker
    with no break, no reversal and nothing else scored 50 on flow and news
    alone and was opened as a paper position.

    The paper book is the one thing that decides whether the rule works. A
    book full of non-setups cannot answer that question.
    """
    nothing = {"broke_level": False, "reversal": False, "direction": "call",
               "remaining_atr": 2.0, "atr": 1.0, "level": 100.0,
               "target": 101.5, "stop": 99.0, "close": 99.5,
               "closed_beyond": False, "volume_ratio": 0.4}
    assert not technical.has_setup(nothing)
    sent, recorded = _run_watchlist_scan(
        monkeypatch, nothing, C.PAPER_THRESHOLD + 15,
        str(tmp_path / "s1.json"))
    assert sent == [], "a name with no break reached Telegram"
    assert recorded == [], "a name with no break opened a paper position"


def test_a_break_under_the_room_rule_still_reaches_the_paper_book(
        monkeypatch, tmp_path):
    """The fix must not close the band it was protecting: a REAL break with
    room left, just under the rule's minimum, is exactly what 944 is for."""
    late = {"broke_level": True, "reversal": False, "direction": "call",
            "remaining_atr": C.MIN_REMAINING_ATR - 0.2, "atr": 1.0,
            "level": 100.0, "target": 101.5, "stop": 99.0, "close": 100.6,
            "closed_beyond": True, "volume_ratio": 2.0}
    sent, recorded = _run_watchlist_scan(
        monkeypatch, late, C.PAPER_THRESHOLD + 15,
        str(tmp_path / "s2.json"))
    assert sent == [], "a break under the room rule must not be alerted"
    assert len(recorded) == 1, "the paper book lost the trade it exists to measure"


def test_a_real_break_is_still_allowed_into_the_paper_book():
    broke = {"broke_level": True, "direction": "call", "atr": 1.0,
             "level": 100.0, "remaining_atr": C.MIN_REMAINING_ATR - 0.1}
    assert technical.has_setup(broke)
    assert technical.is_near_miss(broke)


def test_a_reversal_counts_as_a_setup_for_the_paper_book():
    rev = {"reversal": True, "broke_level": False, "direction": "call",
           "atr": 1.0, "level": 100.0,
           "remaining_atr": C.MIN_REMAINING_ATR - 0.1}
    assert technical.has_setup(rev)


# ── 2. The monitor could not see a reversal ─────────────────────
def test_the_monitor_looks_for_a_reversal_when_no_break_confirms(monkeypatch):
    """The scanner tries technical.reversal() when nothing confirms. The
    monitor re-derived the same bar with analyse() alone — and a reversal
    never sets broke_level, so confirms() rejected it every time.

    One setup, two answers, five minutes apart: the scanner alerts the MSFT
    bottom Salem asked for by name, and the monitor calls the same bar "no
    break" and sends a watch notice instead.
    """
    seen = {}
    flat = {"broke_level": False, "direction": "call", "close": 100.0,
            "level": 101.0, "atr": 1.0, "remaining_atr": 2.0,
            "closed_beyond": False, "volume_ratio": 0.5,
            "bar_high": 100.5, "bar_low": 99.5}
    reversed_tech = dict(flat, reversal=True, level=99.0, target=100.5,
                         stop=98.5, remaining_atr=2.0)

    monkeypatch.setattr(monitor, "load_json",
                        lambda *a: [{"ticker": "MSFT", "direction": "call"}])
    monkeypatch.setattr(monitor.state, "capacity_left", lambda: 5)
    monkeypatch.setattr(monitor.state, "read", lambda: {"alerted_tickers": []})
    monkeypatch.setattr(monitor.uw, "candles", lambda *a, **k: ["bar"])
    monkeypatch.setattr(monitor.technical, "analyse", lambda *a, **k: flat)
    monkeypatch.setattr(monitor.technical, "confirms", lambda t: False)
    monkeypatch.setattr(monitor.technical, "reversal",
                        lambda c, *a, **k: seen.setdefault("asked", True)
                        and ("call", reversed_tech))
    monkeypatch.setattr(C, "WATCH_NOTICE", 0)
    monitor.check_shortlist(dry_run=True)
    assert seen.get("asked"), "the monitor never asked whether a break FAILED"


# ── 3. The 1m re-check guarded one send path, not both ──────────
def test_the_monitor_applies_the_same_1m_recheck_as_the_scanner(monkeypatch):
    """verify.blocks was wired into both paths on purpose. still_holding was
    wired into one, so an alert leaving through the monitor skipped the gate
    that removed four disasters out of twenty-three.
    """
    import inspect
    src = inspect.getsource(monitor.check_shortlist)
    assert "still_holding" in src, (
        "the monitor can reach Telegram without the 1m re-check")


def test_still_holding_reads_the_same_way_from_either_caller(monkeypatch):
    """The monitor passes a hand-built dict rather than a scanner candidate.
    It has to carry the two keys still_holding actually reads."""
    monkeypatch.setattr(C, "USE_MINUTE_CONFIRM", True)
    monkeypatch.setattr(scanner.uw, "last_closed_minute",
                        lambda t: {"close": 99.0})
    cand = {"ticker": "MSFT", "technical": {"direction": "call",
                                            "level": 100.0}}
    assert scanner.still_holding(cand) is False
    monkeypatch.setattr(scanner.uw, "last_closed_minute",
                        lambda t: {"close": 101.0})
    assert scanner.still_holding(cand) is True
