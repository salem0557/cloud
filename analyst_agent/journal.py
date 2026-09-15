"""What actually happened to each call.

A miss proves nothing on its own — the verdict states the hit rate it needs to
break even (usually 30-45%), so most plans are expected to miss and still be
worth taking. The only way to know whether the agent is good is to record every
call and check, later, whether the first target or the stop came first.

Storage is one JSONL file, append-only. On a container filesystem it survives
until the next deploy; mount a volume and set ANALYST_DATA_DIR to keep it.
"""
from __future__ import annotations

import json
import logging
import pathlib
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from . import config, frames, market

log = logging.getLogger(__name__)

OPEN = "open"
TARGET = "target"
STOP = "stop"
UNDECIDED = "undecided"


@dataclass
class Call:
    id: str
    at: str                 # ISO timestamp of the call
    symbol: str
    frame: str
    side: str               # long | short
    entry: float
    stop: float
    targets: list[float]
    conviction: int
    rr: float | None = None
    source: str = "ask"     # ask | auto
    outcome: str = OPEN
    resolved_at: str | None = None
    r_multiple: float | None = None
    note: str = ""
    conflicts: list[str] = field(default_factory=list)
    # Where the call was posted, so the outcome can quote it.
    chat_id: int | None = None
    message_id: int | None = None
    topic_id: int | None = None
    notified: bool = False


def path() -> pathlib.Path:
    return pathlib.Path(config.DATA_DIR) / config.JOURNAL_FILE


def _read() -> list[Call]:
    file = path()
    if not file.exists():
        return []
    out: list[Call] = []
    for line in file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(Call(**json.loads(line)))
        except Exception:
            continue            # a torn line must not lose the whole history
    return out[-config.JOURNAL_MAX_RECORDS:]


def _write(calls: list[Call]) -> None:
    file = path()
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("\n".join(json.dumps(asdict(c), ensure_ascii=False)
                                  for c in calls[-config.JOURNAL_MAX_RECORDS:]) + "\n")
    except Exception:
        log.warning("could not write the journal", exc_info=True)


def record(*, symbol: str, frame: str, verdict: dict, source: str = "ask") -> Call | None:
    """Log a call that carries a real plan. Returns None when there is nothing
    to judge later (a flat verdict has no entry and no stop)."""
    if not config.JOURNAL_ENABLED or not verdict.get("entry") or not verdict.get("stop"):
        return None
    call = Call(
        id=uuid.uuid4().hex[:10],
        at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        symbol=symbol, frame=frame, side=verdict["side"],
        entry=float(verdict["entry"]), stop=float(verdict["stop"]),
        targets=[float(t) for t in (verdict.get("targets") or [])],
        conviction=int(verdict.get("conviction") or 0),
        rr=verdict.get("rr"), source=source,
        conflicts=list(verdict.get("conflicts") or [])[:3],
    )
    try:
        file = path()
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("a") as handle:
            handle.write(json.dumps(asdict(call), ensure_ascii=False) + "\n")
    except Exception:
        log.warning("could not append to the journal", exc_info=True)
        return None
    return call


def bars_after(call: Call, df):
    """Only the bars that printed after the call was made.

    Index timezones differ per asset (New York for equities, UTC for crypto),
    so the cut is done in the index's own zone rather than by string order.
    """
    import pandas as pd

    start = pd.Timestamp(call.at)
    index_tz = getattr(df.index, "tz", None)
    if index_tz is not None:
        start = (start.tz_localize("UTC") if start.tz is None else start).tz_convert(index_tz)
    elif start.tz is not None:
        start = start.tz_convert("UTC").tz_localize(None)
    return df[df.index > start]


def attach_message(call_id: str, chat_id: int, message_id: int,
                   topic_id: int | None = None) -> None:
    """Remember where a call was posted — the message its outcome will quote."""
    calls = _read()
    for call in calls:
        if call.id == call_id:
            call.chat_id, call.message_id, call.topic_id = chat_id, message_id, topic_id
            _write(calls)
            return


def pending_notifications() -> list[Call]:
    """Resolved calls that were posted somewhere and not yet reported back."""
    wanted = {TARGET, STOP} | ({UNDECIDED} if config.FOLLOWUP_UNDECIDED else set())
    return [c for c in _read()
            if c.outcome in wanted and c.message_id and not c.notified]


def mark_notified(call_ids: list[str]) -> None:
    calls = _read()
    ids = set(call_ids)
    for call in calls:
        if call.id in ids:
            call.notified = True
    _write(calls)


def outcome_message(call: Call) -> str:
    """What to say under the original call when it resolves."""
    side_ar = "شراء" if call.side == "long" else "بيع"
    target = call.targets[0] if call.targets else None
    if call.outcome == TARGET and target is not None:
        move = abs(target - call.entry) / call.entry * 100
        return (f"✅ تحقق الهدف الأول — {call.symbol}\n"
                f"صفقة {side_ar} من {call.entry} إلى {target} (+{move:.2f}%)\n"
                f"الربح {call.r_multiple}R. أغلق جزءاً وانقل الستوب لنقطة الدخول للباقي.")
    if call.outcome == STOP:
        move = abs(call.stop - call.entry) / call.entry * 100
        return (f"🛑 وصل الستوب — {call.symbol}\n"
                f"صفقة {side_ar} من {call.entry} إلى {call.stop} (-{move:.2f}%)\n"
                f"اخرج. الخسارة 1R وهي محسوبة في الخطة من البداية.")
    return (f"⌛ انتهت المدة — {call.symbol}\n"
            f"لا الهدف تحقق ولا الستوب ضُرب. النتيجة {call.r_multiple}R.")


def _judge(call: Call, df) -> tuple[str, float | None, str]:
    """Stop or first target — whichever the bars reached first."""
    after = bars_after(call, df)
    if after is None or after.empty:
        return OPEN, None, ""

    risk = abs(call.entry - call.stop)
    if risk <= 0:
        return UNDECIDED, None, "خطة بلا مخاطرة قابلة للقياس"
    target = call.targets[0] if call.targets else None
    window = after.head(config.JOURNAL_MAX_BARS)

    for _, bar in window.iterrows():
        if call.side == "long":
            if bar["Low"] <= call.stop:
                return STOP, -1.0, "ضرب الستوب"
            if target is not None and bar["High"] >= target:
                return TARGET, round((target - call.entry) / risk, 2), "بلغ الهدف الأول"
        else:
            if bar["High"] >= call.stop:
                return STOP, -1.0, "ضرب الستوب"
            if target is not None and bar["Low"] <= target:
                return TARGET, round((call.entry - target) / risk, 2), "بلغ الهدف الأول"

    if len(after) >= config.JOURNAL_MAX_BARS:
        last = float(window["Close"].iloc[-1])
        move = (last - call.entry) if call.side == "long" else (call.entry - last)
        return UNDECIDED, round(move / risk, 2), "انتهت المدة بلا هدف ولا ستوب"
    return OPEN, None, ""


def evaluate(limit: int | None = None) -> list[Call]:
    """Resolve every open call whose bars have printed since. Returns the
    calls that changed state in this pass."""
    calls = _read()
    changed: list[Call] = []
    for call in calls:
        if call.outcome != OPEN:
            continue
        try:
            df = market.fetch(call.symbol, frames.get(call.frame))
        except Exception:
            log.warning("journal: fetch failed for %s", call.symbol, exc_info=True)
            continue
        if df is None or df.empty:
            continue
        outcome, r_multiple, note = _judge(call, df)
        if outcome == OPEN:
            continue
        call.outcome, call.r_multiple, call.note = outcome, r_multiple, note
        call.resolved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        changed.append(call)
        if limit and len(changed) >= limit:
            break
    if changed:
        _write(calls)
    return changed


def stats(source: str | None = None) -> str:
    """The honest scoreboard: hit rate measured against the rate needed."""
    calls = [c for c in _read() if source is None or c.source == source]
    if not calls:
        return ("لا توجد توصيات مسجّلة بعد.\n"
                "كل تحليل فيه خطة يُسجَّل تلقائياً، ويُقيَّم بعد أن تُطبع شموعه.")
    resolved = [c for c in calls if c.outcome in (TARGET, STOP, UNDECIDED)]
    wins = [c for c in resolved if c.outcome == TARGET]
    losses = [c for c in resolved if c.outcome == STOP]
    undecided = [c for c in resolved if c.outcome == UNDECIDED]
    open_calls = [c for c in calls if c.outcome == OPEN]

    lines = [
        "📒 سجل التوصيات",
        f"الإجمالي: {len(calls)} | مُقيَّمة: {len(resolved)} | ما زالت مفتوحة: {len(open_calls)}",
    ]
    if resolved:
        hit_rate = len(wins) / len(resolved) * 100
        lines.append(f"بلغت الهدف: {len(wins)} | ضربت الستوب: {len(losses)} | "
                     f"بلا حسم: {len(undecided)}")
        lines.append(f"نسبة الإصابة: {hit_rate:.0f}%")
        rr_values = [c.rr for c in resolved if c.rr]
        if rr_values:
            average_rr = sum(rr_values) / len(rr_values)
            needed = 100 / (1 + average_rr)
            verdict = "أعلى من المطلوب ✅" if hit_rate >= needed else "أقل من المطلوب ⚠️"
            lines.append(f"متوسط العائد/المخاطرة المستهدف: {average_rr:.2f} → "
                         f"التعادل يحتاج {needed:.0f}% — النتيجة {verdict}")
        r_values = [c.r_multiple for c in resolved if c.r_multiple is not None]
        if r_values:
            lines.append(f"متوسط النتيجة: {sum(r_values) / len(r_values):+.2f}R "
                         f"(المجموع {sum(r_values):+.1f}R)")
        by_conviction = [c for c in resolved if c.conviction >= 70]
        if by_conviction:
            strong_wins = sum(1 for c in by_conviction if c.outcome == TARGET)
            lines.append(f"عند ثقة 70%+: {strong_wins}/{len(by_conviction)} "
                         f"({strong_wins / len(by_conviction) * 100:.0f}%)")
    lines.append("")
    lines.append("ملاحظة: خسارة صفقة لا تعني خطأ النظام — الخطة نفسها تذكر نسبة "
                 "الإصابة اللازمة للتعادل، وهي غالباً 30-45%.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="سجل التوصيات ونتائجها")
    parser.add_argument("--evaluate", action="store_true", help="قيّم التوصيات المفتوحة")
    parser.add_argument("--source", choices=["ask", "auto"], help="اقصر التقرير على مصدر")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    if args.evaluate:
        changed = evaluate()
        print(f"تم حسم {len(changed)} توصية")
        for call in changed:
            print(f"  {call.symbol} {call.side} → {call.outcome} ({call.r_multiple:+.2f}R)"
                  if call.r_multiple is not None else
                  f"  {call.symbol} {call.side} → {call.outcome}")
    print(stats(args.source))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
