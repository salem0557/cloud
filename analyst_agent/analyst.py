"""The orchestrator: one Telegram message in, one chart plus one Arabic read out.

Order of operations, and why:
  1. the caption is parsed first — an explicitly written frame or ticker is the
     most deliberate signal the user can give;
  2. the screenshot fills only what the caption left blank (asset, frame, what
     the user drew);
  3. bars are downloaded for that frame and the frame above it;
  4. indicators and the verdict are computed from those bars;
  5. the chart is drawn from the same numbers the text will quote;
  6. Groq writes the Arabic read around the verdict — and if Groq is down, the
     template fallback answers instead of failing.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from . import (chart as chart_mod, config, frames, groq_client, indicators,
               market, news as news_mod, prompts, session as session_mod, symbols,
               verdict as verdict_mod)

log = logging.getLogger(__name__)

NO_SYMBOL = (
    "ما عرفت الرمز 🤔\n"
    "أرسل الصورة ومعها الرمز والفريم، مثال:\n"
    "• «حلل TSLA فريم 15 دقيقة»\n"
    "• «أرامكو يومي»\n"
    "• «BTC 4 ساعات»"
)
NO_DATA = ("جبت الرمز {symbol} لكن ما توفرت بيانات كافية له على فريم {frame}.\n"
           "جرّب فريم أعلى، أو تأكد من الرمز.")
OUT_OF_MARKET = ("الرمز {symbol} خارج السوق الذي أنا مضبوط عليه (السوق الأمريكي).\n"
                 "لو تبيني أغطّيه، غيّر ANALYST_US_ONLY=false في ملف .env.")


@dataclass
class Answer:
    ok: bool
    text: str
    headline: str = ""
    chart_png: bytes | None = None
    symbol: str | None = None
    frame_key: str | None = None
    used_model: str | None = None
    debug: dict = field(default_factory=dict)


def clean_question(caption: str | None) -> str | None:
    """Drop trigger words and bot mentions so the question reads naturally."""
    if not caption:
        return None
    text = re.sub(r"@[\w_]+", " ", caption)
    for trigger in config.TRIGGERS:
        text = re.sub(rf"(?<![\w؀-ۿ]){re.escape(trigger)}(?![\w؀-ۿ])", " ", text,
                      flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" .،:؛-")
    return text or None


def _pick_symbol(caption: str | None, read) -> tuple[list, str]:
    """Candidate symbols, best first, plus how the winner was identified."""
    from_caption = symbols.resolve(caption) if caption else []
    from_image = []
    if read and read.symbol:
        from_image = [symbols.Candidate(read.symbol, "image", "screenshot", 0.8)]

    strong_caption = [c for c in from_caption if c.confidence >= 0.85]
    if strong_caption:
        return strong_caption + from_image + from_caption, "نص الرسالة"
    if from_image:
        return from_image + from_caption, "الصورة"
    return from_caption, "نص الرسالة"


def _pick_frame(caption: str | None, read, default: str | None) -> tuple[frames.Frame, str]:
    written = frames.parse(caption) if caption else None
    if written:
        return written, "مكتوب في الرسالة"
    if read and read.frame_key:
        return frames.get(read.frame_key), "مقروء من الصورة"
    return frames.get(default or config.DEFAULT_FRAME), "افتراضي"


def analyze(caption: str | None = None, image: bytes | None = None,
            default_frame: str | None = None, with_news: bool = True) -> Answer:
    """Full pipeline. Never raises: every failure returns a readable Answer."""
    question = clean_question(caption)
    read = None
    if image and config.GROQ_API_KEY:
        read = vision_read(image)
    elif image and not config.GROQ_API_KEY:
        log.warning("image received but GROQ_API_KEY is not set — skipping vision")

    candidates, symbol_source = _pick_symbol(caption, read)
    if not candidates:
        # Distinguish "I found nothing" from "I found something I do not cover".
        elsewhere = symbols.resolve(caption, all_markets=True) if caption else []
        if read and read.symbol_raw and not elsewhere:
            elsewhere = symbols.resolve(read.symbol_raw, all_markets=True)
        if elsewhere:
            return Answer(False, OUT_OF_MARKET.format(symbol=elsewhere[0].symbol),
                          symbol=elsewhere[0].symbol)
        hint = ""
        if read and read.error:
            hint = f"\n(قراءة الصورة تعذّرت: {read.error})"
        elif read and not read.is_chart:
            hint = "\n(الصورة لا تبدو تشارت)"
        return Answer(False, NO_SYMBOL + hint, debug={"vision": read.to_dict() if read else None})

    frame, frame_source = _pick_frame(caption, read, default_frame)

    data = market.load(candidates, frame)
    if data is None:
        return Answer(False, NO_DATA.format(symbol=candidates[0].symbol, frame=frame.label_ar),
                      symbol=candidates[0].symbol, frame_key=frame.key)

    used = data.frame
    facts = indicators.analyze(data.df, used.key, daily_df=data.daily_df)
    facts["frame_label"] = used.label_ar
    facts["frame_source"] = frame_source
    # A US read has to state which session it is looking at, and how old the
    # last candle is in its own bar units.
    facts["session"] = session_mod.state()
    facts["session"].update(session_mod.bar_freshness(data.last_time.to_pydatetime(),
                                                      used.minutes))
    context_facts = None
    if data.context_df is not None and len(data.context_df) >= 30:
        ctx_frame = frames.context_frame(used)
        context_facts = indicators.analyze(data.context_df, ctx_frame.key if ctx_frame else "1d",
                                           daily_df=data.daily_df)

    call = verdict_mod.decide(facts, context_facts)
    verdict_dict = call.to_dict()

    news = news_mod.bundle(data.symbol, data.meta) if with_news else {}
    # English label only — the chart image must stay free of Arabic text.
    png = chart_mod.render(data.symbol, data.df, used.label_en, facts, verdict_dict)

    note = data.fallback_note
    text, model_used = _write_analysis(
        symbol=data.symbol, meta=data.meta, requested=frame, used=used, facts=facts,
        context_facts=context_facts, verdict=verdict_dict, news=news,
        read=read, question=question, note=note,
    )

    headline = (f"{data.symbol} • {used.label_ar} • {facts['price']} — "
                f"{call.direction} (ثقة {call.conviction}%)")
    return Answer(
        ok=True, text=text, headline=headline, chart_png=png, symbol=data.symbol,
        frame_key=used.key, used_model=model_used,
        debug={"symbol_source": symbol_source, "frame_source": frame_source,
               "requested_frame": frame.key, "used_frame": used.key,
               "bars": data.bars, "score": round(call.score, 1),
               "vision": read.to_dict() if read else None},
    )


def vision_read(image: bytes):
    """Screenshot identification, isolated so a vision failure is never fatal."""
    from . import vision
    try:
        return vision.read_chart(image)
    except Exception:
        log.exception("vision read crashed")
        return vision.ChartRead(error="خطأ غير متوقع في قراءة الصورة")


def _write_analysis(*, symbol, meta, requested, used, facts, context_facts,
                    verdict, news, read, question, note) -> tuple[str, str | None]:
    """Groq writes the read; the template writes it if Groq cannot."""
    payload = prompts.build_payload(
        symbol=symbol, meta=meta, requested_frame=requested.label_ar,
        used_frame=used.label_ar, facts=facts, context_facts=context_facts,
        verdict=verdict, news=news, chart_read=read.to_dict() if read else None,
        fallback_note=note,
    )
    if config.GROQ_API_KEY:
        try:
            model = groq_client.resolve_model("text")
            text = groq_client.chat(prompts.build_messages(payload, question), kind="text",
                                    model=model)
            if text and len(text) > 120:
                if note and note not in text:
                    text += f"\n\nℹ️ {note}"
                return text, model
            log.warning("Groq reply too short (%s chars); using template", len(text or ""))
        except groq_client.GroqError as exc:
            log.warning("Groq analysis failed: %s", exc)
        except Exception:
            log.exception("Groq analysis crashed")
    return prompts.fallback_text(symbol=symbol, frame_label=used.label_ar, facts=facts,
                                 verdict=verdict, news=news, note=note), None
