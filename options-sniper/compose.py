"""Arabic alert composition.

Two paths, same numbers:
  * `claude -p` renders the template from CLAUDE.md (Salem's original design)
  * a deterministic Python formatter, used as an automatic fallback whenever the
    CLI is missing, times out, errors, or answers NO_TRADE on complete data.

Neither path may produce a number that is not in the payload: the Python
formatter can only read fields, and Claude is told the same in CLAUDE.md.
"""
import json
import subprocess

import config as C

NO_TRADE = "NO_TRADE"

DIRECTION_AR = {"call": "📈 كول", "put": "📉 بوت"}


def _required_present(p):
    tech = p.get("technical") or {}
    needed = [p.get("ticker"), p.get("score"), p.get("direction"), p.get("spot"),
              tech.get("target"), tech.get("stop"), tech.get("entry_rule")]
    return all(v not in (None, "", 0) for v in needed)


# ── Deterministic renderer ──────────────────────────────────────
def render_entry(p):
    tech = p["technical"]
    lines = [
        (f"🚨 {p['ticker']} — تنبيه دخول (نقاط: {p['score']}/100"
         + (f" بعد خصم {p['risk']['penalty']:g} مخاطر)"
            if (p.get('risk') or {}).get('penalty') else ")")),
        "",
        f"الاتجاه: {DIRECTION_AR.get(p['direction'], p['direction'])} ({p.get('flow_reason', 'تدفق خيارات غير اعتيادي')})",
        f"السعر الحالي للسهم: ${p['spot']:.2f}",
        f"الهدف: ${tech['target']:.2f} (كسر ${tech['level']:.2f} + {C.TARGET_ATR_MULT}×ATR)",
        f"نقطة الدخول: {tech['entry_rule']}",
        f"   ← الكسر مؤكد على شمعة 15د مُغلقة ({tech.get('bar_time', '')})",
        f"وقف الخسارة (السهم): ${tech['stop']:.2f}",
        "",
        "العقود المرشحة:",
    ]
    for t in p.get("tiers", []):
        if not t.get("option_symbol"):
            lines.append(f"{t['tier']}: لا يوجد عقد مناسب (سيولة/سعر)")
            continue
        kind = "C" if t["type"] == "call" else "P"
        dte = t.get("dte")
        tag = " ⚡0DTE" if dte == 0 else (f" ({dte}ي)" if dte is not None else "")
        lines.append(
            f"{t['tier']}: {t['strike']:g}{kind}{tag} @ ${t['ask']:.2f} → "
            f"تكلفة ${t['cost']:.0f} — ربح متوقع ~{t['expected_profit_pct']:.0f}%"
        )
    plans = {}
    for t in p.get("tiers", []):
        e = t.get("exit")
        if e:
            plans.setdefault((e["take_pct"], e["stop_pct"], e["note"]), []).append(t["tier"][0])
    if plans:
        lines += ["", "خطة الخروج (على العقد لا السهم):"]
        for (take, stop, note), marks in plans.items():
            who = " ".join(marks)
            lines.append(f"{who} جني ربح +{take}% | وقف خسارة {stop}%")
            if note:
                lines.append(f"   ← {note}")
    if any(t.get("dte") == 0 for t in p.get("tiers", [])):
        lines.append(f"   ← اخرج من 0DTE قبل {C.ZERO_DTE_HARD_EXIT_ET} بتوقيت نيويورك مهما كانت النتيجة")

    expiries = [t.get("expiry") for t in p.get("tiers", []) if t.get("expiry")]
    lines += [
        "",
        f"انتهاء الصلاحية: {sorted(set(expiries))[0] if expiries else '—'}",
        f"⏰ {p.get('time_riyadh', '')}",
        "⚠️ الربح المتوقع تقدير (دلتا+جاما−ثيتا) وليس ضماناً",
    ]

    a = p.get("analyst")
    if a:
        head = {"TAKE": "✅ رأي المحلل", "WAIT": "⏸ رأي المحلل",
                "SKIP": "⛔ رأي المحلل"}.get(a.get("verdict"), "رأي المحلل")
        lines += ["", f"{head} — قناعة {a.get('conviction', '؟')}"]
        br = a.get("base_rate") or {}
        if br.get("available"):
            lines.append(f"   المعدل التاريخي لهذا النوع: {br.get('hit_rate')}% "
                         f"(ن={br.get('count')}) — تقييمه {a.get('vs_base_rate', '؟')}")
        else:
            lines.append(f"   ⚠️ بلا مرجع تاريخي ({br.get('reason', 'غير متاح')})")
        if a.get("reading"):
            lines.append(f"   {a['reading']}")
        for c in (a.get("concerns") or [])[:3]:
            lines.append(f"   • {c}")
        if a.get("tier"):
            lines.append(f"   العقد المفضّل لدى المحلل: {a['tier']}")

    flags = (p.get("risk") or {}).get("flags") or []
    if flags:
        penalty = p["risk"]["penalty"]
        lines += ["", f"⚠️ مخاطر محسوبة (خُصمت {penalty:g} نقطة):"]
        lines += [f"   • {f}" for f in flags]
    if p.get("news"):
        lines += ["", "📰 " + " | ".join(str(n) for n in p["news"][:2])]
    return "\n".join(lines)


def render_exit(p):
    return "\n".join([
        f"🔔 {p['ticker']} — تنبيه خروج",
        "",
        f"النوع: {p['type']}",
        f"العقد: {p['contract']}",
        f"عند الدخول: ${p['entry_price']:.2f} ← الآن: ${p['current_price']:.2f} ({p['pct']:+.1f}%)",
        f"السبب: {p.get('reason', 'بلوغ حد الخروج المضبوط في config.py')}",
        "",
        f"التوصية: {p.get('advice', 'بيع كامل')}",
        f"⏰ {p.get('time_riyadh', '')}",
    ])


# ── Claude path ─────────────────────────────────────────────────
def _via_claude(kind, payload):
    tmpl = "Entry" if kind == "entry" else "Exit"
    prompt = (
        f"Compose the Arabic {tmpl} alert using the {tmpl} Alert Template in "
        "CLAUDE.md. Use ONLY the numbers in this JSON — do not compute, round, "
        "or add anything. Output the message only, no preamble.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    try:
        out = subprocess.run(["claude", "-p", prompt], capture_output=True,
                             text=True, cwd=C.BASE_DIR,
                             timeout=C.CLAUDE_TIMEOUT_SEC)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"[compose] claude unavailable ({type(e).__name__}) — using local formatter")
        return None
    if out.returncode != 0:
        print("[compose] claude failed:", (out.stderr or "")[:200])
        return None
    return out.stdout.strip() or None


def compose(kind, payload):
    """-> message string, or a 'NO_TRADE: reason' string."""
    if kind == "entry" and not _required_present(payload):
        return f"{NO_TRADE}: بيانات ناقصة (هدف/وقف/سعر)"

    if C.USE_CLAUDE_COMPOSER:
        msg = _via_claude(kind, payload)
        if msg and not msg.startswith(NO_TRADE):
            return msg
        if msg and msg.startswith(NO_TRADE):
            # Claude refused on data we already validated -> trust the data,
            # but keep the refusal visible in the log.
            print("[compose] claude returned:", msg[:120], "— falling back")
    return render_entry(payload) if kind == "entry" else render_exit(payload)
