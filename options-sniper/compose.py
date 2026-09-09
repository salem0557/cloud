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
import scoring

NO_TRADE = "NO_TRADE"

DIRECTION_AR = {"call": "📈 كول", "put": "📉 بوت"}


def _required_present(p):
    tech = p.get("technical") or {}
    needed = [p.get("ticker"), p.get("score"), p.get("direction"), p.get("spot"),
              tech.get("target"), tech.get("stop"), tech.get("entry_rule")]
    return all(v not in (None, "", 0) for v in needed)


# ── Deterministic renderer ──────────────────────────────────────
def _expiry_tag(t):
    """" (17 سبتمبر · 9 أيام)" — which contract this price belongs to."""
    exp = (t.get("expiry") or "")[:10]
    if not exp:
        return ""
    dte = t.get("dte")
    day = ""
    try:
        y, m, d = (int(x) for x in exp.split("-"))
        day = f"{d} {AR_MONTHS[m - 1]}"
    except (ValueError, IndexError):
        day = exp
    return f" ({day}" + (f" · {_days_ar(dte)})" if dte else ")")


def _days_ar(n):
    """Arabic counts days by form, not by appending a plural. 1 يوم, 2 يومان,
    3-10 أيام, 11+ يوم — writing "9 يوم" reads as broken Arabic."""
    n = int(n)
    if n == 1:
        return "يوم"
    if n == 2:
        return "يومان"
    return f"{n} أيام" if 3 <= n <= 10 else f"{n} يوم"


AR_MONTHS = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
             "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")


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
    if tech.get("reversal"):
        # A different trade from a breakout and it must not read like one: the
        # level was pierced and reclaimed, the stop is the wick that failed
        # rather than an ATR multiple, and it is bought INTO weakness.
        head += "  🔄"
    if tech.get("opening_range"):
        # A different trade, and it must not read like the others. The level is
        # the first half hour's range rather than intraday structure, the move
        # is faster, and the reversal rate at the open is higher — so it is
        # named on the alert instead of being folded in silently.
        head += "  🌅"
    lines = [head]
    if tech.get("reversal"):
        lines.append("🔄 كسر كاذب — البائعون اخترقوا المستوى وما ثبتوا، "
                     f"والوقف تحت قاع الشمعة {tech['stop']:.2f}")
    if tech.get("opening_range"):
        lines.append("🌅 كسر نطاق الافتتاح — أسرع وأخطر من المعتاد")
    # "اذا تجمعت كل العوامل و التحليلات تدعم توقعك" — the four scores ARE that
    # question, and until now the alert folded them into one number, so a 88
    # built on three strong reads and one weak one looked identical to a 88
    # where everything agreed. Salem judges by whether the factors line up, so
    # he is shown whether they line up.
    b = p.get("score_breakdown") or {}
    if b:
        w = C.WEIGHTS
        parts = [f"{ar} {b[k]:.0f}/{w[k]}" for k, ar in
                 (("flow", "تدفق"), ("technical", "فني"),
                  ("catalyst", "خبر"), ("liquidity", "سيولة")) if k in b]
        if parts:
            lines.append(" · ".join(parts))
    lines.append("")

    # WHY, in Salem's own order: the stock first, the contract second.
    chain = (p.get("reasoning") or {}).get("links") or []
    if chain:
        lines += [l["text"] for l in chain]
    else:                                   # no chain -> say the essentials
        lines += [
            f"كسر {tech['level']:.2f}، والمتوقع يوصل {tech['target']:.2f}",
            f"لو رجع {'تحت' if up else 'فوق'} {tech['stop']:.2f} — اخرج",
        ]
    # Salem, 2026-09-09: "مايكون مقلب ويقلب علي السعر باقل من دقيقة".
    # The stop sits a full ATR past the level; this line is the minute-scale
    # test, and it is a statement about the SETUP, not an instruction to sell:
    # once price is back on the wrong side of the level the break has failed,
    # whatever he then decides to do about it. Measured on NVDA over five
    # sessions, one signal in five went against the entry by 0.51 ATR inside
    # the first minute and never came back.
    if tech.get("level") is not None and not tech.get("reversal"):
        lines.append(f"❗ الكسر يفشل لو رجع {'تحت' if up else 'فوق'} "
                     f"{tech['level']:.2f} — هذا مقياسك بأول دقيقة")
    for gap in (p.get("reasoning") or {}).get("gaps") or []:
        lines.append(f"⚠️ {gap}")

    lines += ["", "اشترِ الآن:"]
    for t in p.get("tiers", []):
        if not t.get("option_symbol"):
            lines.append(f"{t['tier']}: ما فيه عقد مناسب")
            continue
        kind = "كول" if t["type"] == "call" else "بوت"
        # The EXPIRY, on every line, always. The three budget bands are picked
        # independently across the whole 0-45 DTE window, so they routinely come
        # from different expiries — and without the date on the line there is no
        # way to tell which contract a price belongs to. On 2026-09-08 an alert
        # offered "89 بوت @ $1.36", "85 بوت @ $0.74" and "90 بوت @ $0.41": for
        # one expiry a lower put strike is always cheaper, so those three cannot
        # be the same expiry, and Salem priced the wrong contract and found the
        # number wrong. He was right — the message was ambiguous, not the price.
        tag = " ⚡اليوم" if t.get("dte") == 0 else _expiry_tag(t)
        # The ceiling, per contract. Salem reads the alert minutes after it is
        # sent, and by then the contract may have moved: "لي ان شاهدته ارتفع
        # قبل دخولي اتجاهله". Everything measured assumes entry at the price
        # below, so the message says out loud where that stops being true.
        cap = t["ask"] * (1 + C.MAX_CHASE_PCT / 100.0)
        lines.append(f"{t['tier']}: {t['strike']:g} {kind}{tag} @ ${t['ask']:.2f} "
                     f"→ {t['cost']:.0f}$ للعقد")
        # What the stock has to do before this is merely even. Salem believes
        # any move in the stock pays; it does not, because he buys at the ask,
        # sells at the bid and pays a fee each way. Putting the number on
        # every alert answers that once per alert instead of once in a chat.
        even = scoring.breakeven_move(t)
        tail = f" · يتعادل لو تحرك السهم {even:.2f}$" if even else ""
        lines.append(f"    لا تشتري فوق ${cap:.2f}{tail}")

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
        # The paper book has always closed a position after MAX_HOLD_MIN and
        # scored it as a timeout, and the alert never mentioned that a clock
        # existed. So the record was being kept against a rule its reader had
        # never been told, and a trade he held for an hour was compared to one
        # the book abandoned in fifteen minutes.
        lines.append(f"ما تحرك خلال {C.MAX_HOLD_MIN} دقيقة؟ اخرج — الفكرة ماتت")
        lines.append(f"اخرج قبل {C.ZERO_DTE_HARD_EXIT_ET} نيويورك مهما صار")

    if p.get("caution"):
        lines += ["", f"⚠️ {p['caution']}"]
    # The price is read at the moment the message is built, so this stamp is
    # also the price's age. Without it he cannot tell a fresh quote from one
    # that sat in a retry queue.
    lines += ["",
              "↩️ اكتب «اشتريت سترايك ...» لتسجيلها، و«خرجت ...» عند البيع",
              f"⏰ {p.get('time_riyadh', '')} — هذا سعر تلك اللحظة",
              "تحقق من السعر قبل الشراء. الأرقام تقديرية لا مضمونة."]
    a = p.get("analyst")
    if a and a.get("reading"):
        lines += ["", f"🧠 {a['reading']}"]
    return "\n".join(lines)


def render_watch(p):
    """The early notice. Deliberately NOT an alert, and it says so twice.

    A confirmed break is the only thing this system has measured an edge on.
    This message goes out before that, on Salem's explicit ask to be early —
    so it names what has NOT happened yet, and what would have to.
    """
    tech, up = p["technical"], p["direction"] == "call"
    lines = [f"👀 مراقبة — {p['ticker']} {'كول 📈' if up else 'بوت 📉'}",
             "",
             f"السعر {tech['close']:.2f}، و{'المقاومة' if up else 'الدعم'} "
             f"{tech['level']:.2f} — باقي {abs(tech['level'] - tech['close']):.2f}$",
             f"لسه ما كسر. التأكيد = إغلاق شمعة 15د "
             f"{'فوق' if up else 'تحت'} {tech['level']:.2f} بحجم أعلى من المعتاد"]
    m = p.get("magnet")
    if m:
        lines += ["",
                  f"💰 الفلوس داخلة على إضراب {m['strike']:g} — "
                  f"صافي {m['net_premium']/1e6:.1f}M$ شراء، "
                  f"{m['share']*100:.0f}% من تدفق اليوم",
                  f"   (على بُعد {m['distance_pct']:.1f}% من السعر — "
                  "هذا مكان رهانهم، مو وعد إنه يوصله)"]
    lines += ["",
              "⚠️ هذا مو تنبيه دخول. الدخول المقاس يجي بعد التأكيد.",
              f"⏰ {p.get('time_riyadh', '')}"]
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
    # The watch notice is deterministic only. It exists to say what has NOT
    # happened, and a composer that reworded it into something that sounds
    # like a signal would defeat the entire point of separating the two.
    if kind == "watch":
        return render_watch(payload)

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
    if kind == "watch":
        return render_watch(payload)
    return render_entry(payload) if kind == "entry" else render_exit(payload)
