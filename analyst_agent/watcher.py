"""Automatic recommendations: scan a watchlist on a schedule and post the
setups that clear the bar into the recommendations topic.

The "conditions" are not a second opinion — they are the same deterministic
verdict the interactive answer is built on, with thresholds on top
(conviction, score, risk/reward, ADX, volume). So a posted call and an asked-for
call can never contradict each other.

Guard rails, all configurable:
  * long only by default — a squeeze breaks the small stop this project insists on;
  * nothing posted while that asset's market is shut (crypto excepted, it never shuts);
  * no symbol twice inside a cooldown window unless its direction flipped;
  * caps per run and per day, so the topic stays readable;
  * earnings inside a few days skips the symbol: a gap ignores any stop.
"""
from __future__ import annotations

import json
import logging
import pathlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import (chart as chart_mod, config, frames, indicators, market,
               news as news_mod, prompts, session as session_mod, verdict as verdict_mod)

log = logging.getLogger(__name__)


@dataclass
class Alert:
    symbol: str
    side: str
    conviction: int
    text: str
    chart_png: bytes | None
    frame_key: str
    verdict: dict = field(default_factory=dict)


# --- state (best effort: a restart may lose it, which only risks a repeat) ---
def _state_path() -> pathlib.Path:
    return pathlib.Path(config.STATE_FILE)


def load_state() -> dict:
    try:
        return json.loads(_state_path().read_text())
    except Exception:
        return {"posted": {}, "day": "", "count": 0}


def save_state(state: dict) -> None:
    try:
        _state_path().write_text(json.dumps(state, ensure_ascii=False))
    except Exception:
        log.warning("could not persist watcher state", exc_info=True)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _roll_day(state: dict) -> dict:
    if state.get("day") != _today():
        state["day"] = _today()
        state["count"] = 0
    return state


def sides_allowed() -> set[str]:
    if config.WATCH_SIDES in ("both", "all", "كلاهما"):
        return {"long", "short"}
    return {config.WATCH_SIDES} if config.WATCH_SIDES in ("long", "short") else {"long"}


def eligible(facts: dict, call: dict, state: dict, symbol: str) -> tuple[bool, str]:
    """Does this setup clear the bar? Returns (yes, reason-if-no)."""
    if call["side"] not in sides_allowed():
        return False, f"side {call['side']} not enabled"
    if not call.get("entry") or not call.get("stop"):
        return False, "no trade plan"
    if call["conviction"] < config.WATCH_MIN_CONVICTION:
        return False, f"conviction {call['conviction']} < {config.WATCH_MIN_CONVICTION}"
    if abs(call["score"]) < config.WATCH_MIN_SCORE:
        return False, f"score {call['score']} < {config.WATCH_MIN_SCORE}"
    if (call.get("rr") or 0) < config.WATCH_MIN_RR:
        return False, f"rr {call.get('rr')} < {config.WATCH_MIN_RR}"
    adx = facts["trend"].get("adx")
    if adx is not None and adx < config.WATCH_MIN_ADX:
        return False, f"adx {adx} < {config.WATCH_MIN_ADX}"
    rel = facts["volume"].get("relative")
    if rel is not None and rel < config.WATCH_MIN_REL_VOLUME:
        return False, f"relative volume {rel} < {config.WATCH_MIN_REL_VOLUME}"
    if config.WATCH_ONLY_WHEN_OPEN and not (facts.get("session") or {}).get("is_open"):
        return False, "market closed"

    previous = (state.get("posted") or {}).get(symbol)
    if previous and previous.get("side") == call["side"]:
        age_hours = (time.time() - previous.get("ts", 0)) / 3600
        if age_hours < config.WATCH_COOLDOWN_HOURS:
            return False, f"posted {age_hours:.1f}h ago (cooldown)"
    return True, ""


def _evaluate(symbol: str, frame) -> tuple[dict, dict] | None:
    """Technical pass for one symbol: no company info, no news — speed first."""
    data = market.load([symbol], frame, with_meta=False)
    if data is None or data.bars < config.MIN_BARS:
        return None
    facts = indicators.analyze(data.df, data.frame.key, daily_df=data.daily_df)
    facts["frame_label"] = data.frame.label_ar
    facts["session"] = session_mod.state_for(symbol)
    context_facts = None
    if data.context_df is not None and len(data.context_df) >= 30:
        ctx = frames.context_frame(data.frame)
        context_facts = indicators.analyze(data.context_df, ctx.key if ctx else "1d",
                                           daily_df=data.daily_df)
    call = verdict_mod.decide(facts, context_facts).to_dict()
    facts["_df"] = data.df           # kept for the chart, stripped before the text
    facts["_symbol"] = data.symbol
    return facts, call


def scan(symbols: list[str] | None = None, frame_key: str | None = None,
         force: bool = False) -> list[Alert]:
    """One pass over the watchlist. Returns the alerts worth posting."""
    frame = frames.get(frame_key or config.WATCH_FRAME)
    watchlist = symbols or config.WATCHLIST
    state = _roll_day(load_state())
    if not force and state["count"] >= config.WATCH_MAX_PER_DAY:
        log.info("daily alert cap reached (%s)", state["count"])
        return []

    passed: list[tuple[dict, dict]] = []
    for symbol in watchlist:
        try:
            evaluated = _evaluate(symbol, frame)
        except Exception:
            log.warning("evaluate failed for %s", symbol, exc_info=True)
            continue
        if not evaluated:
            continue
        facts, call = evaluated
        ok, why = eligible(facts, call, state, symbol)
        if not ok:
            log.debug("%s skipped: %s", symbol, why)
            continue
        passed.append((facts, call))

    passed.sort(key=lambda pair: -pair[1]["conviction"])
    alerts: list[Alert] = []
    for facts, call in passed:
        if len(alerts) >= config.WATCH_MAX_PER_RUN:
            break
        if not force and state["count"] + len(alerts) >= config.WATCH_MAX_PER_DAY:
            break
        symbol = facts.pop("_symbol")
        df = facts.pop("_df")

        # Second pass, only for the few that passed: company info and events.
        meta, events = {}, {}
        try:
            meta = market._meta(symbol)
            events = news_mod.event_risk(meta)
        except Exception:
            log.warning("meta/events failed for %s", symbol, exc_info=True)
        days = events.get("days_to_earnings")
        if days is not None and 0 <= days <= config.WATCH_SKIP_EARNINGS_DAYS:
            log.info("%s skipped: earnings in %s days", symbol, days)
            continue

        text = prompts.alert_text(
            symbol=symbol, name=meta.get("name"), frame_label=frame.label_ar,
            facts=facts, verdict=call, events=events)
        png = chart_mod.render(symbol, df, frame.label_en, facts, call)
        alerts.append(Alert(symbol=symbol, side=call["side"],
                            conviction=call["conviction"], text=text,
                            chart_png=png, frame_key=frame.key, verdict=call))
    return alerts


def mark_posted(alerts: list[Alert]) -> None:
    """Record what went out, so the cooldown and the daily cap mean something."""
    state = _roll_day(load_state())
    posted = state.setdefault("posted", {})
    for alert in alerts:
        posted[alert.symbol] = {"ts": time.time(), "side": alert.side,
                                "conviction": alert.conviction}
    state["count"] = state.get("count", 0) + len(alerts)
    save_state(state)


def destination() -> tuple[int, int | None] | None:
    """(chat_id, topic_id) to post into, or None if nothing is configured."""
    chat = config.ALERTS_CHAT
    if not chat and len(config.ALLOWED_CHATS) == 1:
        chat = next(iter(config.ALLOWED_CHATS))
    if not chat:
        return None
    return chat, (config.ALERTS_TOPIC or None)


def enabled() -> bool:
    return bool(config.WATCH_ENABLED and destination())


def status() -> str:
    """Human-readable watcher state, used by /diag and /watchlist."""
    target = destination()
    state = _roll_day(load_state())
    if not target:
        return ("التوصيات التلقائية معطّلة: أضف ANALYST_ALERTS_CHAT "
                "(ورقم القسم في ANALYST_ALERTS_TOPIC).")
    chat, topic = target
    return "\n".join([
        f"التوصيات التلقائية: {'مفعّلة ✅' if config.WATCH_ENABLED else 'موقوفة ⛔'}",
        f"الوجهة: {chat}" + (f" / قسم {topic}" if topic else ""),
        f"كل {config.WATCH_INTERVAL_MIN} دقيقة | فريم {frames.get(config.WATCH_FRAME).label_ar}",
        f"الشروط: ثقة ≥ {config.WATCH_MIN_CONVICTION}% | نقاط ≥ {config.WATCH_MIN_SCORE} | "
        f"R:R ≥ {config.WATCH_MIN_RR} | ADX ≥ {config.WATCH_MIN_ADX} | "
        f"فوليوم ≥ {config.WATCH_MIN_REL_VOLUME}×",
        f"الاتجاه: {'شراء وبيع' if len(sides_allowed()) == 2 else 'شراء فقط' if 'long' in sides_allowed() else 'بيع فقط'}",
        f"الحد: {config.WATCH_MAX_PER_RUN} لكل دورة و {config.WATCH_MAX_PER_DAY} يومياً "
        f"(أُرسل اليوم: {state.get('count', 0)})",
        f"المراقَبة ({len(config.WATCHLIST)}): " + " ".join(config.WATCHLIST[:18])
        + (" …" if len(config.WATCHLIST) > 18 else ""),
    ])
