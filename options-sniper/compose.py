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
    """The alert, deterministic. This is the path Railway takes.

    Salem read the first version and said it was hard to follow. It was: it led
    with a score, then flow jargon, then a target, then three contracts, then a
    separate exit plan — five blocks before the first instruction. And the
    reasoning chain, the part that says WHY, never appeared here at all. It had
    been wired into the payload and into the Claude composer's instructions,
    and this renderer — the one that actually runs, since USE_CLAUDE_COMPOSER
    is 0 on the container — was never given it.

    So the order is now the order a decision is made in: what happened, where
    it goes, what kills it, what to buy, when to get out. Anything that is not
    one of those is gone.
    """
    tech = p["technical"]
    up = p["direction"] == "call"
    head = f"🚨 {p['ticker']} — {'كول 📈' if up else 'بوت 📉'}"
    if (p.get("risk") or {}).get("penalty"):
        head += f"  ({p['score']}/100 بعد خصم المخاطر)"
    else:
        head += f"  ({p['score']}/100)"
    lines = [head, ""]

    # WHY, in Salem's own order: the stock first, the contract second.
    chain = (p.get("reasoning") or {}).get("links") or []
    if chain:
        lines += [l["text"] for l in chain]
    else:                                   # no chain -> say the essentials
        lines += [
            f"كسر {tech['level']:.2f}، والمتوقع يوصل {tech['target']:.2f}",
            f"لو رجع {'تحت' if up else 'فوق'} {tech['stop']:.2f} — اخرج",
        ]
    for gap in (p.get("reasoning") or {}).get("gaps") or []:
        lines.append(f"⚠️ {gap}")

    lines += ["", "اشترِ الآن:"]
    for t in p.get("tiers", []):
        if not t.get("option_symbol"):
            lines.append(f"{t['tier']}: ما فيه عقد مناسب")
            continue
        kind = "كول" if t["type"] == "call" else "بوت"
        tag = " ⚡اليوم" if t.get("dte") == 0 else ""
        lines.append(f"{t['tier']}: {t['strike']:g} {kind}{tag} @ ${t['ask']:.2f} "
                     f"→ {t['cost']:.0f}$ للعقد")

    # One exit line, not a table. Tiers almost always share a rule; when they
    # do not, the differing one gets its own line rather than a legend.
    plans = {}
    for t in p.get("tiers", []):
        e = t.get("exit")
        if e:
            plans.setdefault((e["take_pct"], e["stop_pct"]), []).append(t["tier"][0])
    if plans:
        lines.append("")
        for (take, stop), marks in plans.items():
            who = "" if len(plans) == 1 else " ".join(marks) + "  "
            lines.append(f"{who}بِع عند +{take}%  |  اقطع عند {stop}%")
    if any(t.get("dte") == 0 for t in p.get("tiers", [])):
        lines.append(f"اخرج قبل {C.ZERO_DTE_HARD_EXIT_ET} نيويورك مهما صار")

    lines += ["", f"⏰ {p.get('time_riyadh', '')} — الأرقام تقديرية لا مضمونة"]
    a = p.get("analyst")
    if a and a.get("reading"):
        lines += ["", f"🧠 {a['reading']}"]
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
