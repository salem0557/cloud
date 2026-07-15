"""/review and /stats: scores logged signals against real market data and
reports aggregate performance. Reads scanner/signals_db.py's `signals`
table -- never writes a new signal itself, bot.py's session runner does
that as each scan result streams in (see bot.py's `_log_row`).

run_review() is the only piece that mutates the database (writes outcomes
back); compute_stats() is purely read-only aggregation over whatever's
already been reviewed.
"""
import asyncio
import datetime as dt
import logging
import time

from . import config, crypto_data, data, options
from . import signals_db as db

log = logging.getLogger(__name__)

_STOCK_CRYPTO_SECTIONS = ("stocks", "crypto")
_OPTIONS_FAMILY_SECTIONS = ("options", "leaps", "heavy", "golden", "whale")

SECTION_LABELS_AR = {
    "stocks": "📈 الأسهم", "crypto": "🪙 الكريبتو", "options": "📊 الأوبشن",
    "leaps": "🗓️ LEAPS", "heavy": "🏛️ Heavy", "golden": "⭐ الذهبية",
    "whale": "🐋 الحيتان",
}
FILTER_LABELS_AR = {
    "bollinger": "بولينجر السفلي", "rsi": "RSI تشبع بيعي",
    "support": "منطقة دعم", "wedge": "وتد هابط", "volume": "حجم متزايد",
}
TIER_LABELS_AR = {"gold": "🥇 ممتاز", "silver": "🥈 جيد جداً", "bronze": "🥉 مقبول"}
CATEGORY_LABELS_AR = {"mega": "🏛️ MEGA", "large": "🏢 LARGE", "etf": "📦 ETF"}
WHALE_TIER_LABELS_AR = {"unusual": "🟠 شاذ", "whale": "🐋 حوت شبه مؤكد"}


# --------------------------------------------------------------- /review

def _hit_by_move(entry: float | None, current: float) -> bool:
    if not entry:
        return False
    return (current - entry) / entry * 100 >= config.REVIEW_HIT_MOVE_PCT


def _review_stock_crypto(rows: list) -> dict[int, tuple[float, str]]:
    """(signal_id -> (current_price, outcome)) -- stock symbols batched in
    one yfinance call, crypto symbols iterated (ccxt has no batch ticker
    endpoint here)."""
    results: dict[int, tuple[float, str]] = {}
    stock_rows = [r for r in rows if r["section"] == "stocks"]
    crypto_rows = [r for r in rows if r["section"] == "crypto"]

    if stock_rows:
        symbols = sorted({r["symbol"] for r in stock_rows})
        try:
            frames = data.fetch_batch(symbols, "1d", "5d")
        except Exception:
            log.exception("Review: stock batch price fetch failed")
            frames = {}
        for r in stock_rows:
            df = frames.get(r["symbol"])
            if df is None or df.empty:
                continue
            current = float(df["Close"].iloc[-1])
            results[r["id"]] = (current, "hit" if _hit_by_move(r["underlying_price"], current)
                                else "miss")

    for r in crypto_rows:
        current = crypto_data.fetch_last_price(r["symbol"])
        if current is None:
            continue
        results[r["id"]] = (current, "hit" if _hit_by_move(r["underlying_price"], current)
                            else "miss")

    return results


def _review_options_family(rows: list) -> dict[int, tuple[float | None, str]]:
    """(signal_id -> (current_premium_or_None, outcome)). Live contract
    lookup while the expiry hasn't passed ("hit" = premium above entry,
    i.e. the position would show an unrealized profit); once the contract
    is expired (or its quote can't be found), the outcome falls back to an
    approximation -- underlying price now above the strike -- since Yahoo
    no longer serves a quote for an expired contract. That's a real
    simplification worth knowing about: it uses TODAY's underlying price,
    not the price on the actual expiry date, so it can be off for a
    contract that expired ITM but the stock has since fallen back below
    strike (or vice versa)."""
    results: dict[int, tuple[float | None, str]] = {}
    today = dt.date.today()
    fallback_rows = []

    for r in rows:
        expiry_date = None
        if r["expiry"]:
            try:
                expiry_date = dt.date.fromisoformat(r["expiry"])
            except ValueError:
                pass

        if expiry_date and expiry_date >= today:
            premium = options.fetch_contract_premium(r["symbol"], r["strike"], r["expiry"],
                                                      is_call=True)
            if premium is not None:
                outcome = "hit" if premium > (r["contract_price"] or 0) else "miss"
                results[r["id"]] = (premium, outcome)
                continue
        fallback_rows.append(r)

    if fallback_rows:
        symbols = sorted({r["symbol"] for r in fallback_rows})
        try:
            frames = data.fetch_batch(symbols, "1d", "5d")
        except Exception:
            log.exception("Review: options-family fallback price fetch failed")
            frames = {}
        for r in fallback_rows:
            df = frames.get(r["symbol"])
            if df is None or df.empty:
                continue
            underlying_now = float(df["Close"].iloc[-1])
            results[r["id"]] = (None, "hit" if underlying_now > r["strike"] else "miss")

    return results


async def run_review() -> dict:
    """Reviews every signal due at its 7-day and/or 30-day checkpoint (up
    to REVIEW_MAX_PER_RUN each), scores it, and writes the outcome back.
    Returns a summary for format_review_summary()."""
    summary: dict = {"windows": {}, "reviewed": 0, "hits": 0}
    now = time.time()

    for window_days in config.REVIEW_WINDOWS_DAYS:
        due = await asyncio.to_thread(db.fetch_due_for_review, window_days, now,
                                      config.REVIEW_MAX_PER_RUN)
        if not due:
            summary["windows"][window_days] = {"reviewed": 0, "hits": 0}
            continue

        sc_rows = [r for r in due if r["section"] in _STOCK_CRYPTO_SECTIONS]
        opt_rows = [r for r in due if r["section"] in _OPTIONS_FAMILY_SECTIONS]
        sc_results = await asyncio.to_thread(_review_stock_crypto, sc_rows)
        opt_results = await asyncio.to_thread(_review_options_family, opt_rows)

        combined = {**sc_results, **opt_results}
        updates = [(signal_id, price, outcome) for signal_id, (price, outcome) in combined.items()]
        await asyncio.to_thread(db.bulk_update_outcomes, window_days, updates, now)

        hits = sum(1 for _, _, outcome in updates if outcome == "hit")
        summary["windows"][window_days] = {"reviewed": len(updates), "hits": hits}
        summary["reviewed"] += len(updates)
        summary["hits"] += hits

    summary["open_total"] = await asyncio.to_thread(db.count_open_signals)
    return summary


def format_review_summary(summary: dict) -> str:
    lines = ["📋 *تقرير /review*", ""]
    hit_cap = False
    for window_days in config.REVIEW_WINDOWS_DAYS:
        w = summary["windows"].get(window_days, {"reviewed": 0, "hits": 0})
        if w["reviewed"] == 0:
            lines.append(f"• {window_days} يوم: لا إشارات مستحقة للمراجعة الآن")
        else:
            rate = w["hits"] / w["reviewed"] * 100
            lines.append(f"• {window_days} يوم: رُوجعت {w['reviewed']} إشارة "
                        f"— إصابة {rate:.0f}% ({w['hits']}/{w['reviewed']})")
        if w["reviewed"] >= config.REVIEW_MAX_PER_RUN:
            hit_cap = True
    lines.append("")
    if hit_cap:
        lines.append("⏳ وصلت سقف المراجعة لهذا التشغيل — شغّل /review مرة ثانية لمتابعة الباقي.")
    lines.append(f"إجمالي الإشارات المفتوحة (لم تكتمل مراجعتها لـ30 يوم بعد): {summary['open_total']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- /stats

def _split_filters(s: str | None) -> list[str]:
    return [f for f in (s or "").split(",") if f]


def _new_bucket() -> dict:
    return {7: {"hits": 0, "total": 0}, 30: {"hits": 0, "total": 0}}


def _tally(bucket: dict, window: int, outcome: str) -> None:
    bucket[window]["total"] += 1
    if outcome == "hit":
        bucket[window]["hits"] += 1


async def compute_stats() -> dict:
    rows = await asyncio.to_thread(db.fetch_reviewed_signals)
    stats: dict = {
        "total_reviewed": len(rows),
        "by_section": {}, "by_filter": {}, "by_tier": {}, "by_category": {}, "by_whale_tier": {},
        "avg_move": {}, "avg_premium_move": {}, "best_combos": [],
    }
    if not rows:
        return stats

    move_accum: dict[tuple[str, int], list[float]] = {}
    combo_buckets: dict[tuple[str, str], dict] = {}

    for r in rows:
        section = r["section"]
        is_stock_crypto = section in _STOCK_CRYPTO_SECTIONS
        stats["by_section"].setdefault(section, _new_bucket())
        for window in (7, 30):
            outcome = r[f"outcome_{window}d"]
            if outcome is None:
                continue
            _tally(stats["by_section"][section], window, outcome)
            price = r[f"review_price_{window}d"]
            # Stock/crypto rows store the reviewed UNDERLYING price here, so
            # this is a straight underlying-price move. Options-family rows
            # store the reviewed CONTRACT PREMIUM instead (a different
            # quantity from the entry underlying_price) -- comparing them
            # would be nonsense, so those move against contract_price
            # (entry premium) instead, and only when a live premium was
            # actually found (the post-expiry approximation path leaves
            # this None, correctly excluding those signals here).
            entry = r["underlying_price"] if is_stock_crypto else r["contract_price"]
            if price is not None and entry:
                accum_key = (section, window) if is_stock_crypto else ("premium:" + section, window)
                move_accum.setdefault(accum_key, []).append((price - entry) / entry * 100)

        filters_matched = r["filters_matched"]
        if section in _STOCK_CRYPTO_SECTIONS:
            for f in _split_filters(filters_matched):
                stats["by_filter"].setdefault(f, _new_bucket())
                for window in (7, 30):
                    outcome = r[f"outcome_{window}d"]
                    if outcome is not None:
                        _tally(stats["by_filter"][f], window, outcome)
            if filters_matched:
                combo_key = (section, filters_matched)
                combo_buckets.setdefault(combo_key, _new_bucket())
                for window in (7, 30):
                    outcome = r[f"outcome_{window}d"]
                    if outcome is not None:
                        _tally(combo_buckets[combo_key], window, outcome)
        elif section in ("options", "leaps") and filters_matched:
            stats["by_tier"].setdefault(filters_matched, _new_bucket())
            for window in (7, 30):
                outcome = r[f"outcome_{window}d"]
                if outcome is not None:
                    _tally(stats["by_tier"][filters_matched], window, outcome)
        elif section == "heavy" and filters_matched:
            stats["by_category"].setdefault(filters_matched, _new_bucket())
            for window in (7, 30):
                outcome = r[f"outcome_{window}d"]
                if outcome is not None:
                    _tally(stats["by_category"][filters_matched], window, outcome)
        elif section == "whale" and filters_matched:
            stats["by_whale_tier"].setdefault(filters_matched, _new_bucket())
            for window in (7, 30):
                outcome = r[f"outcome_{window}d"]
                if outcome is not None:
                    _tally(stats["by_whale_tier"][filters_matched], window, outcome)

    for (key, window), values in move_accum.items():
        avg = sum(values) / len(values)
        if key.startswith("premium:"):
            stats["avg_premium_move"].setdefault(key[len("premium:"):], {})[window] = avg
        else:
            stats["avg_move"].setdefault(key, {})[window] = avg

    ranked = []
    for (section, combo), windows in combo_buckets.items():
        if windows[30]["total"] >= config.REVIEW_MIN_COMBO_SAMPLE:
            rate, n, basis = windows[30]["hits"] / windows[30]["total"], windows[30]["total"], 30
        elif windows[7]["total"] >= config.REVIEW_MIN_COMBO_SAMPLE:
            rate, n, basis = windows[7]["hits"] / windows[7]["total"], windows[7]["total"], 7
        else:
            continue
        ranked.append((section, combo, rate, n, basis))
    ranked.sort(key=lambda t: t[2], reverse=True)
    stats["best_combos"] = ranked[:3]

    return stats


def _rate_str(bucket: dict) -> str:
    if bucket["total"] == 0:
        return "لا بيانات"
    return f"{bucket['hits'] / bucket['total'] * 100:.0f}% ({bucket['hits']}/{bucket['total']})"


def format_stats_report(stats: dict) -> str:
    if stats["total_reviewed"] == 0:
        return ("📊 *تقرير الأداء*\n\n"
                "ما فيه إشارات رُوجعت بعد — شغّل /review أولاً بعد ما تعدّي إشارات 7 أو 30 يوم.")

    lines = ["📊 *تقرير الأداء* (من السجل التاريخي)", "", "*نسبة الإصابة حسب القسم:*"]
    for section, windows in stats["by_section"].items():
        label = SECTION_LABELS_AR.get(section, section)
        lines.append(f"{label} — 7ي: {_rate_str(windows[7])} · 30ي: {_rate_str(windows[30])}")

    if stats["by_filter"]:
        lines += ["", "*نسبة الإصابة حسب الشرط (أسهم/كريبتو):*"]
        for f, windows in stats["by_filter"].items():
            label = FILTER_LABELS_AR.get(f, f)
            lines.append(f"{label} — 7ي: {_rate_str(windows[7])} · 30ي: {_rate_str(windows[30])}")

    if stats["by_tier"]:
        lines += ["", "*نسبة الإصابة حسب الفئة (أوبشن):*"]
        for t, windows in stats["by_tier"].items():
            label = TIER_LABELS_AR.get(t, t)
            lines.append(f"{label} — 7ي: {_rate_str(windows[7])} · 30ي: {_rate_str(windows[30])}")

    if stats["by_category"]:
        lines += ["", "*نسبة الإصابة حسب الفئة (Heavy):*"]
        for c, windows in stats["by_category"].items():
            label = CATEGORY_LABELS_AR.get(c, c)
            lines.append(f"{label} — 7ي: {_rate_str(windows[7])} · 30ي: {_rate_str(windows[30])}")

    if stats["by_whale_tier"]:
        lines += ["", "*نسبة الإصابة حسب تصنيف الحوت:*"]
        for t, windows in stats["by_whale_tier"].items():
            label = WHALE_TIER_LABELS_AR.get(t, t)
            lines.append(f"{label} — 7ي: {_rate_str(windows[7])} · 30ي: {_rate_str(windows[30])}")

    if stats["avg_move"]:
        lines += ["", "*متوسط حركة سعر الأصل (أسهم/كريبتو):*"]
        for section, windows in stats["avg_move"].items():
            label = SECTION_LABELS_AR.get(section, section)
            parts = [f"{n}ي: {pct:+.1f}%" for n, pct in sorted(windows.items())]
            lines.append(f"{label} — " + " · ".join(parts))

    if stats["avg_premium_move"]:
        lines += ["", "*متوسط تغيّر البريميوم (أوبشن، للعقود اللي لسه مدرجة عند المراجعة):*"]
        for section, windows in stats["avg_premium_move"].items():
            label = SECTION_LABELS_AR.get(section, section)
            parts = [f"{n}ي: {pct:+.1f}%" for n, pct in sorted(windows.items())]
            lines.append(f"{label} — " + " · ".join(parts))

    if stats["best_combos"]:
        lines += ["", f"*أفضل {len(stats['best_combos'])} توليفات شروط (أسهم/كريبتو، "
                     f"عينة ≥{config.REVIEW_MIN_COMBO_SAMPLE}):*"]
        for section, combo, rate, n, basis in stats["best_combos"]:
            combo_ar = "، ".join(FILTER_LABELS_AR.get(f, f) for f in combo.split(","))
            label = SECTION_LABELS_AR.get(section, section)
            lines.append(f"{label}: {combo_ar} — {rate * 100:.0f}% ({n} إشارة، أساس {basis} يوم)")

    return "\n".join(lines)
