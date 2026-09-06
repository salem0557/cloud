"""Send one clearly-marked SAMPLE alert, so the format can be seen off-hours.

Salem asked for a test message "about a trade you found". Nothing was found —
the market is closed and no scan has run — so this sends a sample, and says so
in the first line and the last. Sending invented numbers dressed as a live
signal is precisely the failure of the system he ran before this one, where a
win rate derived from an option score was printed as if it were a statistic.

The numbers below are a plausible NVDA setup, not a real one. Everything else
about the message is real: it is rendered by the same compose() the live
scanner uses, from a payload with the same shape, with the reasoning chain
built by the same reasoning.chain(). What arrives on the phone is exactly what
a real alert will look like, minus the truth of the figures.

The exit plan is built by scoring.exit_rule(), not written out here. The first
version hand-wrote it as a tuple where compose() expects a dict and crashed on
send — a sample that drifts from the live shape is not a preview of anything,
so every field it cannot copy verbatim is computed by the same function the
scanner uses.
"""
import sys

import venv_boot

venv_boot.ensure(["requests"])

import reasoning
import config as C
from compose import compose, render_entry
from scoring import exit_rule
from telegram_send import send

BANNER = "🧪 رسالة تجريبية — الأرقام عيّنة وليست صفقة حقيقية\n" + "─" * 28


def payload():
    """A payload shaped exactly like the scanner's, with sample figures."""
    p = {
        "ticker": "NVDA", "score": 89, "raw_score": 89,
        "score_breakdown": {"flow": 26, "technical": 27, "catalyst": 16,
                            "liquidity": 20},
        "risk": {"penalty": 0.0, "flags": []},
        "direction": "call", "spot": 182.90,
        "flow_reason": "شراء كول بـ $2.4M علاوة، 71% عند الطلب، 4 سويب",
        "technical": {
            "broke_level": True, "direction": "call", "level": 182.40,
            "close": 182.90, "atr": 1.80, "target": 185.10, "stop": 180.90,
            "volume_ratio": 2.3, "closed_beyond": True, "expected_move": 2.20,
            "break_distance_atr": 0.28, "remaining_atr": 1.22,
            "entry_rule": "الدخول عند 182.40–182.90 (لا تطارد فوق 183.10)",
        },
        "news": ["Nvidia expands data-centre partnership"],
        "tiers": [
            {"tier": "🟢 <200$", "option_symbol": "NVDA260911C00185000",
             "strike": 185.0, "type": "call", "expiry": "2026-09-11", "dte": 5,
             "ask": 1.85, "bid": 1.78, "cost": 185.0, "delta": 0.44,
             "gamma": 0.09, "theta": -0.12, "open_interest": 4210,
             "expected_profit_pct": 61.0, "exit": exit_rule(5)},
            {"tier": "🟡 <100$", "option_symbol": "NVDA260911C00187500",
             "strike": 187.5, "type": "call", "expiry": "2026-09-11", "dte": 5,
             "ask": 0.95, "bid": 0.90, "cost": 95.0, "delta": 0.31,
             "gamma": 0.11, "theta": -0.10, "open_interest": 2870,
             "expected_profit_pct": 74.0, "exit": exit_rule(5)},
            {"tier": "🔴 <50$", "option_symbol": None},
        ],
        "time_riyadh": "17:42",
    }
    p["reasoning"] = reasoning.chain(p)
    return p


def main(argv=None):
    args = argv if argv is not None else sys.argv[1:]
    dry = "--dry-run" in args
    p = payload()
    # --plain forces the deterministic renderer. Railway runs with
    # USE_CLAUDE_COMPOSER=0, so that is the path a real alert takes there; a
    # local dry run without this flag goes through the composer instead and
    # shows a message Salem will never receive. The first version of this file
    # crashed in render_entry on his container while printing fine on mine,
    # because the test only ever exercised the other path.
    body = (render_entry(p) if "--plain" in args or not C.USE_CLAUDE_COMPOSER
            else compose("entry", p))
    msg = f"{BANNER}\n\n{body}\n\n{'─' * 28}\n🧪 انتهت الرسالة التجريبية"
    if dry:
        print(msg)
        return 0
    ok = send(msg)
    print("sent" if ok else "NOT sent — check the Telegram variables")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
