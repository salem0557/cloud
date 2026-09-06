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
"""
import sys

import venv_boot

venv_boot.ensure(["requests"])

import reasoning
from compose import compose
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
             "expected_profit_pct": 61.0,
             "exit": (60, -40, "بيع نصف الكمية عند الهدف وارفع الوقف")},
            {"tier": "🟡 <100$", "option_symbol": "NVDA260911C00187500",
             "strike": 187.5, "type": "call", "expiry": "2026-09-11", "dte": 5,
             "ask": 0.95, "bid": 0.90, "cost": 95.0, "delta": 0.31,
             "gamma": 0.11, "theta": -0.10, "open_interest": 2870,
             "expected_profit_pct": 74.0,
             "exit": (60, -40, "بيع نصف الكمية عند الهدف وارفع الوقف")},
            {"tier": "🔴 <50$", "option_symbol": None},
        ],
        "time_riyadh": "17:42",
    }
    p["reasoning"] = reasoning.chain(p)
    return p


def main(argv=None):
    dry = "--dry-run" in (argv or sys.argv[1:])
    p = payload()
    body = compose("entry", p)
    msg = f"{BANNER}\n\n{body}\n\n{'─' * 28}\n🧪 انتهت الرسالة التجريبية"
    if dry:
        print(msg)
        return 0
    ok = send(msg)
    print("sent" if ok else "NOT sent — check the Telegram variables")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
