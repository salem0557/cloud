"""Salem's own book: the trades HE takes, with fake money, from the alerts.

The two Telegram sections have two different owners, and only one of them was
built:

    topic 943  the alerts. He reads them and buys with fake money. Nothing
               recorded what he took, so his own results did not exist.
    topic 944  the system's automatic paper test, which works.

Both are fake money. The difference is who decides. The automatic book takes
EVERY alert and scores it against a fixed rule; this one takes only what he
says he took, and lets him say when he is out — because the exit is his:
"اما الخروج فهو علي".

The input is a Telegram REPLY to the alert, so he never has to name a ticker
or a strike; the message he replies to already says which contract it was.
journal.csv has had an empty `outcome` column for weeks waiting to be filled
in by hand, and paper.py's own docstring says nobody fills in a spreadsheet
for a month. A reply is the shortest thing that could work.

    دخلت            entered the first contract in that alert
    دخلت 186        entered the 186 strike from that alert
    خرجت            out now, at whatever it is worth
    خرجت 1.40       out at a price he names

Anything else is ignored in silence. A chat is not a command line and an
error message under every stray word would make the section unusable.
"""
import datetime
import json
import re

import config as C
import market
import uw

MINE_FILE = C.DATA_DIR / "mine.json"
SENT_FILE = C.DATA_DIR / "alerts_sent.json"
OFFSET_FILE = C.DATA_DIR / "tg_offset.json"

# Kept small on purpose: every extra word is another way for a reply to be
# misread as a trade. "دخلت" and "خرجت" are what he would type anyway.
IN_WORDS = ("دخلت", "اشتريت", "شريت", "دخلنا")
OUT_WORDS = ("خرجت", "بعت", "خرجنا", "طلعت")


def _load(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _save(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def remember_alert(message_id, payload):
    """Keep what an alert offered, so a reply to it can be resolved.

    Only the fields a fill needs. The book is the record; this is a lookup.
    """
    if not message_id or message_id is True:
        return
    sent = _load(SENT_FILE, {})
    sent[str(message_id)] = {
        "ticker": payload.get("ticker", ""),
        "direction": payload.get("direction", ""),
        "time_riyadh": payload.get("time_riyadh", ""),
        "tiers": [{k: t.get(k) for k in
                   ("tier", "option_symbol", "strike", "type", "expiry",
                    "ask", "bid", "cost", "delta", "dte")}
                  for t in (payload.get("tiers") or []) if t.get("option_symbol")],
    }
    # A day of alerts is a handful of entries; keeping every one forever would
    # turn a lookup into an archive nobody reads.
    if len(sent) > 400:
        for k in sorted(sent, key=int)[:len(sent) - 400]:
            del sent[k]
    _save(SENT_FILE, sent)


def parse(text):
    """-> ('in'|'out'|None, strike_or_price_or_None). Never raises."""
    if not text:
        return None, None
    t = text.strip()
    num = None
    m = re.search(r"\d+(?:\.\d+)?", t)
    if m:
        try:
            num = float(m.group())
        except ValueError:
            num = None
    if any(w in t for w in IN_WORDS):
        return "in", num
    if any(w in t for w in OUT_WORDS):
        return "out", num
    return None, None


def _pick_tier(tiers, strike):
    if not tiers:
        return None
    if strike is None:
        return tiers[0]
    for t in tiers:
        if t.get("strike") is not None and abs(t["strike"] - strike) < 0.001:
            return t
    return None


def open_position(alert, strike=None, at=None):
    """-> (position, note). Refuses rather than guessing which contract."""
    tier = _pick_tier(alert.get("tiers"), strike)
    if tier is None:
        have = ", ".join(f"{t['strike']:g}" for t in alert.get("tiers") or [])
        return None, f"ما لقيت سترايك {strike:g} في هذا التنبيه. الموجود: {have}"
    book = _load(MINE_FILE, {"open": [], "closed": []})
    if any(p["option_symbol"] == tier["option_symbol"] and p["open"]
           for p in book["open"]):
        return None, "أنت ماسكه أصلاً"
    pos = {
        "ticker": alert.get("ticker", ""), "option_symbol": tier["option_symbol"],
        "strike": tier.get("strike"), "type": tier.get("type"),
        "expiry": tier.get("expiry"), "dte": tier.get("dte"),
        "direction": alert.get("direction") or tier.get("type"),
        "entry_price": at if at else tier.get("ask"),
        "entry_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "entry_date": datetime.date.today().isoformat(),
        # His price when he names one, the alert's ask when he does not. Which
        # it was is recorded, because "he paid what the alert said" and "he
        # told us what he paid" are different facts.
        "price_source": "his" if at else "alert ask",
        "open": True,
    }
    book["open"].append(pos)
    _save(MINE_FILE, book)
    return pos, ""


def close_position(option_symbol=None, at=None):
    """Close one open position -> (position, note)."""
    book = _load(MINE_FILE, {"open": [], "closed": []})
    live = [p for p in book["open"] if p.get("open")]
    if not live:
        return None, "ما عندك صفقة مفتوحة"
    pos = None
    if option_symbol:
        pos = next((p for p in live if p["option_symbol"] == option_symbol), None)
    elif len(live) == 1:
        pos = live[0]
    if pos is None:
        names = "، ".join(f"{p['ticker']} {p['strike']:g}" for p in live)
        return None, f"أي وحدة؟ المفتوح: {names}"
    price = at
    if price is None:
        price = _last_price(pos)
    if not price:
        return None, "ما قدرت أجيب السعر — أرسل: خرجت <السعر>"
    pos.update({
        "open": False, "exit_price": round(float(price), 4),
        "exit_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "exit_source": "his" if at else "tape",
        "multiple": round(float(price) / pos["entry_price"], 4)
        if pos.get("entry_price") else None,
    })
    book["open"] = [p for p in book["open"] if p is not pos]
    book["closed"].append(pos)
    _save(MINE_FILE, book)
    return pos, ""


def _last_price(pos):
    try:
        rows = uw.contract_intraday(pos["option_symbol"], date=pos["entry_date"])
    except Exception:
        return None
    for r in reversed(rows or []):
        px = r.get("close") or r.get("avg_price")
        if px:
            return px
    return None


def apply_reply(update):
    """One Telegram update -> a reply to send back, or ''.

    Silent on anything that is not a reply to a remembered alert carrying one
    of the words. Chat is not a command line.
    """
    msg = (update or {}).get("message") or {}
    reply_to = msg.get("reply_to_message") or {}
    action, num = parse(msg.get("text"))
    if not action:
        return ""
    if action == "out":
        # Closing needs no alert to reply to: he may be flat-out telling us.
        pos, note = close_position(at=num if num and num < 100 else None)
        if not pos:
            return note
        pct = (pos["multiple"] - 1) * 100 if pos.get("multiple") else 0.0
        return (f"✅ سجّلت الخروج — {pos['ticker']} {pos['strike']:g} "
                f"@ ${pos['exit_price']:.2f} ({pct:+.1f}%)")
    sent = _load(SENT_FILE, {})
    alert = sent.get(str(reply_to.get("message_id")))
    if not alert:
        return "رد على رسالة التنبيه نفسها عشان أعرف أي عقد."
    pos, note = open_position(alert, strike=num)
    if not pos:
        return note
    return (f"✅ سجّلت الدخول — {pos['ticker']} {pos['strike']:g} "
            f"@ ${pos['entry_price']:.2f}")


def poll_and_apply(send_fn=None):
    """Read new Telegram messages and act on the ones that are fills."""
    from telegram_send import poll, send
    send_fn = send_fn or send
    off = _load(OFFSET_FILE, {}).get("offset")
    updates, nxt = poll(off)
    acted = 0
    for u in updates:
        try:
            out = apply_reply(u)
        except Exception as e:                  # never take the scheduler down
            print(f"[mine] reply failed: {e}")
            continue
        if out:
            send_fn(out)
            acted += 1
    if nxt != off:
        _save(OFFSET_FILE, {"offset": nxt})
    return acted


def summary(book=None):
    book = book or _load(MINE_FILE, {"open": [], "closed": []})
    done = [p for p in book["closed"] if p.get("multiple")]
    if not done:
        return {"n": 0, "open": len(book["open"])}
    won = sum(1 for p in done if p["multiple"] > 1.0)
    avg = sum(p["multiple"] for p in done) / len(done)
    pnl = sum((p["multiple"] - 1) * (p.get("entry_price") or 0) * 100
              for p in done)
    return {"n": len(done), "open": len(book["open"]),
            "won": won, "win_pct": won / len(done) * 100,
            "avg": avg, "pnl": pnl}


def daily_message(book=None):
    book = book or _load(MINE_FILE, {"open": [], "closed": []})
    today = datetime.date.today().isoformat()
    done = [p for p in book["closed"]
            if (p.get("exit_at") or "")[:10] == today and p.get("multiple")]
    s = summary(book)
    lines = [f"📊 صفقاتك اليوم — {today}", ""]
    if not done:
        lines.append("ما سجّلت أي صفقة اليوم.")
    else:
        for p in done:
            pct = (p["multiple"] - 1) * 100
            lines.append(f"{'✅' if pct > 0 else '❌'} {p['ticker']} "
                         f"{p['strike']:g} {pct:+.1f}%")
        day_pnl = sum((p["multiple"] - 1) * (p.get("entry_price") or 0) * 100
                      for p in done)
        lines.append(f"\nالناتج: {day_pnl:+.0f}$")
    if s.get("n"):
        lines += ["", f"الإجمالي: {s['n']} صفقة، ربحت {s['win_pct']:.0f}%",
                  f"لكل 1$: ${s['avg']:.3f}   ({s['pnl']:+.0f}$)"]
    if book["open"]:
        lines.append(f"\n⏳ مفتوح: {len(book['open'])} — أرسل «خرجت» لإغلاقها")
    return "\n".join(lines)


if __name__ == "__main__":
    print(daily_message())
