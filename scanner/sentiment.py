""""وجهة نظر البوت": merge public news headlines and StockTwits chatter about
a symbol into one short Arabic paragraph via a single Gemini call.

This is pure consolidation, not independent analysis — the prompt explicitly
forbids the model from adding its own opinion or recommendation, only
summarizing the general tilt the sources themselves show. Both data sources
(Yahoo news, StockTwits) are free and keyless; only the summarization call
needs GEMINI_API_KEY. Any failure (missing key, no data, network error)
returns None so the alert still goes out without this section.
"""
import logging

import requests
import yfinance as yf

from . import config

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
STOCKTWITS_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"

PROMPT_TEMPLATE = """اجمع المعلومات التالية عن سهم {symbol} في فقرة عربية واحدة قصيرة (3-4 أسطر كحد أقصى).
لا تُبدِ رأياً أو تحليلاً أو توصية خاصة بك بأي شكل؛ فقط لخّص وادمج ما ورد في المصادر التالية بأسلوب \
محايد، مع ذكر الاتجاه العام (إيجابي/سلبي/متباين) الذي تعكسه هذه المصادر فقط دون أي استنتاج إضافي منك.

إذا كانت المصادر أدناه فارغة أو عامة جداً بحيث لا تكفي لتلخيص فعلي ذي معنى، أجب بكلمة واحدة فقط: \
NONE — لا تكتب فقرة عامة أو حشو بلا مضمون حقيقي في هذه الحالة.

عناوين إخبارية حديثة:
{headlines}

تعليقات متداولين على StockTwits:
{social}
"""


def _get_news_headlines(symbol: str) -> list[str]:
    try:
        items = yf.Ticker(symbol).news or []
    except Exception:
        log.warning("News fetch failed for %s", symbol)
        return []
    headlines = []
    for item in items[:config.SENTIMENT_NEWS_LIMIT]:
        # Newer yfinance nests fields under "content"; older shape is flat.
        content = item.get("content", item)
        title = content.get("title")
        provider = content.get("provider") or {}
        publisher = provider.get("displayName") or item.get("publisher")
        if title:
            headlines.append(f"{title} ({publisher})" if publisher else title)
    return headlines


def _get_social_messages(symbol: str) -> list[str]:
    try:
        resp = requests.get(STOCKTWITS_URL.format(symbol=symbol), timeout=10,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return []
        messages = resp.json().get("messages") or []
    except Exception:
        log.warning("StockTwits fetch failed for %s", symbol)
        return []
    return [m["body"] for m in messages[:config.SENTIMENT_SOCIAL_LIMIT] if m.get("body")]


def _call_gemini(prompt: str) -> str | None:
    if not config.GEMINI_API_KEY:
        return None
    url = GEMINI_URL.format(model=config.GEMINI_MODEL)
    try:
        resp = requests.post(
            url,
            headers={"x-goog-api-key": config.GEMINI_API_KEY,
                     "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": 500,
                    "temperature": 0.2,
                    # Newer Gemini models spend part of maxOutputTokens on
                    # hidden "thinking" before the visible answer, which can
                    # leave a short/blank/truncated-looking summary here.
                    # This call needs no reasoning, just consolidation, so
                    # disable it and give the full budget to the answer.
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
            timeout=20,
        )
        resp.raise_for_status()
        candidates = resp.json().get("candidates") or []
        if not candidates:
            return None
        finish_reason = candidates[0].get("finishReason")
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        if finish_reason == "MAX_TOKENS" and not text:
            log.warning("Gemini summary truncated to nothing (finishReason=MAX_TOKENS)")
        return text or None
    except Exception:
        log.warning("Gemini summarization failed", exc_info=True)
        return None


def get_sentiment_summary(symbol: str) -> str | None:
    """Short Arabic paragraph merging news + social chatter for `symbol`,
    or None if nothing is available, disabled, or the call fails."""
    if not config.SENTIMENT_ENABLED or not config.GEMINI_API_KEY:
        return None
    headlines = _get_news_headlines(symbol)
    social = _get_social_messages(symbol)
    if not headlines and not social:
        return None
    prompt = PROMPT_TEMPLATE.format(
        symbol=symbol,
        headlines="\n".join(f"- {h}" for h in headlines) or "(لا توجد)",
        social="\n".join(f"- {s}" for s in social) or "(لا توجد)",
    )
    summary = _call_gemini(prompt)
    if summary and summary.strip().upper() == "NONE":
        return None
    if summary and len(summary) > config.SENTIMENT_MAX_CHARS:
        summary = summary[:config.SENTIMENT_MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return summary
