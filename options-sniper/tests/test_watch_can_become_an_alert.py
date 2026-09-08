"""A watched name must be capable of becoming an alert.

2026-09-08: every message Salem received was a watch notice. Not one entry
alert. The arithmetic says why.

A watched ticker's score when it finally breaks is base + technical, and
technical is worth at most WEIGHTS["technical"] = 30. With THRESHOLD at 70,
a base below 40 can never reach the gate however cleanly the stock breaks.
WATCHLIST_FLOOR was 30 — so everything scoring 30-39 was put on the watchlist,
sent its heads-up, and was structurally incapable of ever alerting.

Those are messages he can do nothing with, and they were all of them.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config as C


def test_every_watched_name_can_reach_the_alert_gate():
    """The invariant. A name on the watchlist must be able to become an alert
    on a perfect break, or watching it is a promise the system cannot keep.

    The gate that matters is BREAK_THRESHOLD: a name is watched precisely
    because it has NOT broken yet, so the gate it will be judged by when it
    does is the confirmed-break one.
    """
    best_possible = C.WATCHLIST_FLOOR + C.WEIGHTS["technical"]
    assert best_possible >= min(C.THRESHOLD, C.BREAK_THRESHOLD), (
        f"a name at the floor ({C.WATCHLIST_FLOOR}) tops out at {best_possible}, "
        "below every gate — it would be watched forever and could never alert")


def test_the_floor_is_derived_from_the_gate_not_chosen():
    """Move a gate and the floor must follow, or this comes back."""
    assert C.WATCHLIST_FLOOR == (min(C.THRESHOLD, C.BREAK_THRESHOLD)
                                 - C.WEIGHTS["technical"])


def test_a_confirmed_break_is_judged_more_kindly_than_a_forecast():
    """Price agreeing is worth more than flow and a headline agreeing."""
    assert C.BREAK_THRESHOLD < C.THRESHOLD


def test_the_floor_still_sits_below_the_gate():
    """It is a watchlist, not a second alert gate. There has to be room for a
    break to promote something."""
    assert C.WATCHLIST_FLOOR < C.BREAK_THRESHOLD


def test_the_paper_book_still_reaches_below_every_alert_gate():
    """944 exists to measure what 943 rejects. If the paper gate rose to meet
    the lower alert gate there would be nothing left to compare."""
    assert C.PAPER_THRESHOLD < C.BREAK_THRESHOLD < C.THRESHOLD


def test_a_watched_name_can_also_reach_the_paper_gate():
    assert C.WATCHLIST_FLOOR + C.WEIGHTS["technical"] >= C.PAPER_THRESHOLD
