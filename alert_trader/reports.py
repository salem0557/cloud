"""Daily and paper-week reports, computed only from the stored record.

Suggestions at the end are proposals to test; nothing here changes a rule or
a limit.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter

from . import market_calendar as cal
from .storage import iso, parse_iso


def _window(day: dt.date) -> tuple[str, str]:
    start = dt.datetime.combine(day, dt.time(0), cal.NY).astimezone(cal.UTC)
    return iso(start), iso(start + dt.timedelta(days=1))


def _stats(store, mode: str, start: str, end: str) -> dict:
    msgs = store.q("SELECT * FROM messages WHERE received_at>=? AND received_at<?", (start, end))
    alerts = store.q("SELECT * FROM alerts WHERE created_at>=? AND created_at<? AND mode=?",
                     (start, end, mode))
    trades = store.q("SELECT * FROM trades WHERE created_at>=? AND created_at<? AND mode=?",
                     (start, end, mode))
    closed = store.q("SELECT * FROM trades WHERE closed_at>=? AND closed_at<? AND mode=? "
                     "AND status IN ('closed','expired')", (start, end, mode))
    fills = store.q("SELECT f.*, o.side, o.purpose FROM fills f JOIN orders o ON o.id=f.order_id "
                    "WHERE f.ts>=? AND f.ts<? AND o.mode=?", (start, end, mode))
    orders = store.q("SELECT * FROM orders WHERE created_at>=? AND created_at<? AND mode=?",
                     (start, end, mode))
    events = store.q("SELECT * FROM events WHERE ts>=? AND ts<?", (start, end))
    equity = store.q("SELECT * FROM equity WHERE ts>=? AND ts<? AND mode=? ORDER BY ts",
                     (start, end, mode))

    status = Counter(a["status"] for a in alerts)
    kinds = Counter(a["kind"] for a in alerts if a["status"] != "ignored")
    reasons = Counter()
    for a in alerts:
        if a["status"] in ("rejected", "duplicate"):
            for r in json.loads(a["reasons"] or "[]")[:1]:
                reasons[r.split(":")[0].split("(")[0].strip()[:60]] += 1
    parse_errors = sum(1 for a in alerts
                       if json.loads(a["parsed"] or "{}").get("errors"))
    latency = [(parse_iso(m["received_at"]) - parse_iso(m["date"])).total_seconds()
               for m in msgs if m["version"] == 0]
    spreads, devs, sources = [], [], Counter()
    for a in alerts:
        if not a["quote"]:
            continue
        q = json.loads(a["quote"])
        sources[f"{q['source']}{' (متأخر)' if q.get('delayed') else ''}"] += 1
        if q.get("bid") and q.get("ask"):
            mid = (q["bid"] + q["ask"]) / 2
            spreads.append((q["ask"] - q["bid"]) / mid * 100)
            p = json.loads(a["parsed"])
            if p.get("entry_high"):
                devs.append((q["ask"] - p["entry_high"]) / p["entry_high"] * 100)
    slip = [f["slippage"] for f in fills if f["slippage"] is not None]
    fees = sum(f["fees"] for f in fills)
    pnl = [t["realized_pnl"] for t in closed if t["realized_pnl"] is not None]
    peak, dd = None, 0.0
    for e in equity:
        peak = e["equity"] if peak is None else max(peak, e["equity"])
        dd = max(dd, peak - e["equity"])
    return {
        "posts": sum(1 for m in msgs if m["version"] == 0),
        "edits": sum(1 for m in msgs if m["version"] != 0),
        "status": status, "kinds": kinds, "reasons": reasons, "parse_errors": parse_errors,
        "latency": latency, "spreads": spreads, "devs": devs, "sources": sources,
        "trades": trades, "closed": closed, "fills": fills, "orders": orders,
        "partial": sum(1 for o in orders if 0 < (o["filled_qty"] or 0) < o["qty"]
                       or Counter(f["order_id"] for f in fills)[o["id"]] > 1),
        "expired_entries": sum(1 for o in orders if o["side"] == "buy" and o["status"] == "expired"),
        "slip": slip, "fees": fees, "pnl": pnl,
        "exit_reasons": Counter((t["exit_reason"] or "—").split(" (")[0] for t in closed),
        "errors": Counter(e["kind"] for e in events if e["level"] in ("error", "warning")),
        "duplicates": status.get("duplicate", 0) + sum(1 for e in events if e["kind"] == "duplicate_delivery"),
        "foreign": sum(1 for e in events if e["kind"] == "foreign"),
        "equity_first": equity[0]["equity"] if equity else None,
        "equity_last": equity[-1]["equity"] if equity else None,
        "drawdown": dd,
    }


def _avg(xs):
    return sum(xs) / len(xs) if xs else None


def _fmt(x, suffix="", nd=2):
    return "—" if x is None else f"{x:.{nd}f}{suffix}"


def _body(st: dict) -> list[str]:
    s = st["status"]
    lines = [
        "📥 الاستقبال",
        f"• منشورات جديدة: {st['posts']} | تعديلات: {st['edits']} | مكررة: {st['duplicates']}"
        f" | من مصدر غير مسموح: {st['foreign']}",
        f"• متوسط زمن الوصول: {_fmt(_avg(st['latency']), ' ث', 1)}",
        "🔎 التدقيق",
        f"• دخول/تعديل/خروج: {st['kinds'].get('entry', 0)}/{st['kinds'].get('update', 0)}/"
        f"{st['kinds'].get('exit', 0)} | غير مصنفة: {st['kinds'].get('unknown', 0)}",
        f"• مقبولة: {s.get('accepted', 0)} | مرفوضة: {s.get('rejected', 0)} | مطبقة: {s.get('applied', 0)}"
        f" | أخطاء استخراج: {st['parse_errors']}",
    ]
    for reason, n in st["reasons"].most_common(5):
        lines.append(f"   – {reason}: {n}")
    lines += [
        "💲 الأسعار",
        f"• مصادر العرض/الطلب: " + (", ".join(f"{k}×{v}" for k, v in st["sources"].items()) or "—"),
        f"• متوسط فارق العرض/الطلب عند التنبيه: {_fmt(_avg(st['spreads']), '%', 1)}",
        f"• متوسط انحراف الطلب عن سعر التنبيه: {_fmt(_avg(st['devs']), '%', 1)}",
        f"• متوسط الانزلاق لكل تنفيذ: {_fmt(_avg(st['slip']), '$', 3)}",
        "⚙️ التنفيذ",
        f"• صفقات مسجلة: {len(st['trades'])} | أوامر: {len(st['orders'])} | تنفيذات: {len(st['fills'])}"
        f" | جزئية: {st['partial']} | أوامر دخول انتهت دون تنفيذ: {st['expired_entries']}",
        f"• صفقات مغلقة: {len(st['closed'])} — أسباب: "
        + (", ".join(f"{k}×{v}" for k, v in st["exit_reasons"].items()) or "—"),
        "📊 النتائج (بعد التكاليف)",
        f"• صافي محقق: {_fmt(sum(st['pnl']) if st['pnl'] else 0.0, '$')} | الرسوم: {_fmt(st['fees'], '$')}",
        f"• رابحة/خاسرة: {sum(1 for p in st['pnl'] if p > 0)}/{sum(1 for p in st['pnl'] if p <= 0)}",
        f"• قيمة المحفظة: {_fmt(st['equity_first'], '$')} ← {_fmt(st['equity_last'], '$')}"
        f" | أقصى تراجع: {_fmt(st['drawdown'], '$')}",
    ]
    if st["errors"]:
        lines.append("🧯 أعطال وتحذيرات: " + ", ".join(f"{k}×{v}" for k, v in st["errors"].most_common(6)))
    return lines


def daily(engine, day: dt.date | None = None) -> str:
    day = day or cal.ny_date(engine.clock())
    start, end = _window(day)
    st = _stats(engine.store, engine.mode, start, end)
    bounds = cal.session_bounds(day)
    session = ("لا جلسة (عطلة/نهاية أسبوع)" if not bounds else
               f"{'إغلاق مبكر' if cal.is_early_close(day) else 'جلسة كاملة'}: "
               f"{cal.fmt_both(bounds[0])} ← {bounds[1].astimezone(cal.NY):%H:%M} نيويورك")
    head = [f"{engine.tag()} 📅 التقرير اليومي — {day.isoformat()}", f"🕒 {session}", ""]
    return "\n".join(head + _body(st))


def untested(engine, st: dict) -> list[str]:
    out = ["إرسال الأوامر الحقيقية عبر أبيان (محظور في الوضع الورقي — لم يُختبر)"]
    store = engine.store
    if not store.one("SELECT 1 FROM trades WHERE broker_verified=1"):
        out.append("التحقق من العقود في أبيان من داخل البوت (لم ينجح أو لم يُشغّل)")
    if any("متأخر" in k for k in st["sources"]):
        out.append("التنفيذ على أسعار لحظية — المصدر المستخدم متأخر")
    checks = [
        (st["kinds"].get("exit", 0) == 0, "ربط تنبيهات الخروج من المصدر بالصفقات"),
        (st["kinds"].get("update", 0) == 0, "تطبيق تنبيهات التعديل"),
        (st["edits"] == 0, "معالجة الرسائل المعدلة في القناة"),
        (st["partial"] == 0, "التنفيذ الجزئي على أسعار فعلية"),
        (not any("وقف" in k for k in st["exit_reasons"]), "الخروج بوقف الخسارة"),
        (not any("الهدف" in k for k in st["exit_reasons"]), "الخروج عند الأهداف/الخروج الجزئي"),
        (not any("الانتهاء" in k for k in st["exit_reasons"]), "الإغلاق الإلزامي قبل الانتهاء"),
        (not st["errors"].get("data_outage"), "التعافي من انقطاع بيانات الأسعار"),
    ]
    out += [label for missing, label in checks if missing]
    return out


def suggestions(st: dict) -> list[str]:
    out = []
    total = sum(st["status"].values()) or 1
    for reason, n in st["reasons"].most_common(3):
        if n / total >= 0.2:
            out.append(f"«{reason}» سبب {n} رفضًا: راجع هل الحد مناسب، واختبر أي تغيير ورقيًا أولًا.")
    if st["parse_errors"]:
        out.append("أخطاء استخراج موجودة: أرسل أمثلة الرسائل المرفوضة لتحسين المحلل ثم أعد الاختبار عليها.")
    if st["expired_entries"]:
        out.append("أوامر دخول انتهت دون تنفيذ: اختبر مدة صلاحية أطول أو هامش سعر أعلى ضمن حد الانحراف.")
    if _avg(st["latency"]) and _avg(st["latency"]) > 5:
        out.append("زمن وصول التنبيهات مرتفع: قارن طريقة الربط (Bot API مقابل حساب مستخدم).")
    return out or ["لا توجد بيانات كافية لاقتراح تغيير."]


def weekly(engine) -> str:
    start = parse_iso(engine.store.get("paper_week_start"))
    now = engine.clock()
    if start is None:
        return "لم يبدأ أسبوع المحاكاة بعد."
    end = min(now, start + dt.timedelta(days=7))
    st = _stats(engine.store, engine.mode, iso(start), iso(end + dt.timedelta(seconds=1)))
    sessions = cal.sessions_between(start, end)
    pnl = st["pnl"]
    wins = [p for p in pnl if p > 0]
    lines = [
        f"{engine.tag()} 🗓 تقرير أسبوع المحاكاة",
        f"• البداية: {cal.fmt_both(start)}",
        f"• حتى: {cal.fmt_both(end)}" + (" (الأسبوع مكتمل)" if now >= start + dt.timedelta(days=7) else " (جارٍ)"),
        f"• جلسات السوق المشمولة: {len(sessions)} — " + ", ".join(d.isoformat() for d in sessions),
        "",
    ] + _body(st) + [
        f"• نسبة الصفقات الرابحة: {_fmt(len(wins) / len(pnl) * 100 if pnl else None, '%', 0)}",
        f"• متوسط الربح/الخسارة للصفقة: {_fmt(_avg(pnl), '$')}",
        "",
        "🧪 أجزاء لم تُختبر فعليًا:",
    ] + [f"• {x}" for x in untested(engine, st)] + [
        "",
        "💡 اقتراحات قابلة للاختبار (لا تُطبق تلقائيًا):",
    ] + [f"• {x}" for x in suggestions(st)] + [
        "",
        "ℹ️ نتائج أسبوع واحد لا تثبت ربحية مستقبلية. يبقى البوت ورقيًا حتى تفعّل الوضع الحقيقي بنفسك.",
    ]
    return "\n".join(lines)
