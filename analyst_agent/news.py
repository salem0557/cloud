"""News, chatter and event risk — the "everything else" side of the read.

All three sources are free and keyless (Yahoo headlines, StockTwits stream,
Yahoo earnings date). Nothing here is summarised by a model: the raw items go
into the prompt so the analysis can quote them, and a failure just means an
empty list.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests
import yfinance as yf

from . import config

log = logging.getLogger(__name__)

STOCKTWITS = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; analyst-agent/1.0)"}


def _stocktwits_symbol(symbol: str) -> str:
    """StockTwits uses plain tickers; Saudi listings are not covered there."""
    if symbol.endswith(".SR"):
        return ""
    return symbol.replace("-USD", ".X").replace("=F", "").replace("^", "")


def age_hours(published) -> float | None:
    """How old a headline is, from either an epoch int or an ISO string."""
    if published in (None, ""):
        return None
    try:
        if isinstance(published, (int, float)):
            when = datetime.fromtimestamp(float(published), tz=timezone.utc)
        else:
            text = str(published).replace("Z", "+00:00")
            when = datetime.fromisoformat(text)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    return max(0.0, (datetime.now(timezone.utc) - when).total_seconds() / 3600)


def age_label(hours: float | None) -> str:
    if hours is None:
        return ""
    if hours < 1:
        return "قبل دقائق"
    if hours < 24:
        return f"قبل {int(hours)} ساعة"
    return f"قبل {int(hours / 24)} يوم"


def _publisher_rank(publisher: str) -> int:
    """0 for wire services and real outlets, 1 for everything else."""
    low = (publisher or "").lower()
    return 0 if any(name in low for name in config.NEWS_PREFER_PUBLISHERS) else 1


def _is_noise(publisher: str) -> bool:
    low = (publisher or "").lower()
    return any(name in low for name in config.NEWS_SKIP_PUBLISHERS)


def headlines(symbol: str, limit: int | None = None) -> list[dict]:
    """Recent headlines worth showing: reporting first, marketing dropped.

    A "should you buy Costco?" piece from a stock-tips site beside a bearish
    two-hour read reads like a contradiction when it is not even about the
    same horizon. Promotional outlets are skipped and stale items dropped.
    """
    if not config.NEWS_ENABLED:
        return []
    limit = limit or config.NEWS_LIMIT
    try:
        items = yf.Ticker(symbol).news or []
    except Exception:
        log.warning("news fetch failed for %s", symbol, exc_info=True)
        return []
    out = []
    for item in items:
        content = item.get("content", item)  # newer yfinance nests under "content"
        title = content.get("title")
        if not title:
            continue
        provider = content.get("provider") or {}
        publisher = provider.get("displayName") or item.get("publisher") or ""
        if _is_noise(publisher):
            continue
        published = (content.get("pubDate") or content.get("displayTime")
                     or item.get("providerPublishTime") or "")
        hours = age_hours(published)
        if hours is not None and hours > config.NEWS_MAX_AGE_HOURS:
            continue
        out.append({
            "title": title,
            "publisher": publisher,
            "published": published,
            "age_hours": round(hours, 1) if hours is not None else None,
            "age_label": age_label(hours),
            "summary": (content.get("summary") or "")[:400],
        })
    # Real reporting first, then the freshest.
    out.sort(key=lambda h: (_publisher_rank(h["publisher"]),
                            h["age_hours"] if h["age_hours"] is not None else 9e9))
    return out[:limit]


def social(symbol: str, limit: int | None = None) -> dict:
    """StockTwits chatter: messages plus the platform's own bull/bear tally."""
    if not config.SOCIAL_ENABLED:
        return {"messages": [], "bullish": None, "bearish": None}
    st_symbol = _stocktwits_symbol(symbol)
    if not st_symbol:
        return {"messages": [], "bullish": None, "bearish": None}
    limit = limit or config.SOCIAL_LIMIT
    try:
        resp = requests.get(STOCKTWITS.format(symbol=st_symbol), headers=UA,
                            timeout=config.HTTP_TIMEOUT)
        if resp.status_code != 200:
            return {"messages": [], "bullish": None, "bearish": None}
        messages = resp.json().get("messages") or []
    except Exception:
        log.warning("stocktwits failed for %s", symbol, exc_info=True)
        return {"messages": [], "bullish": None, "bearish": None}

    bodies, bull, bear = [], 0, 0
    for message in messages[:limit]:
        body = (message.get("body") or "").strip()
        if body:
            bodies.append(body[:280])
        sentiment = ((message.get("entities") or {}).get("sentiment") or {}).get("basic")
        if sentiment == "Bullish":
            bull += 1
        elif sentiment == "Bearish":
            bear += 1
    return {"messages": bodies, "bullish": bull, "bearish": bear,
            "tilt": ("إيجابي" if bull > bear * 1.5 else
                     "سلبي" if bear > bull * 1.5 else "متباين")}


def event_risk(meta: dict) -> dict:
    """Known scheduled risk — mainly how many days to the next earnings date."""
    out: dict = {}
    raw = meta.get("next_earnings")
    if raw:
        out["next_earnings"] = raw
        try:
            when = datetime.fromisoformat(str(raw)[:10]).replace(tzinfo=timezone.utc)
            days = (when - datetime.now(timezone.utc)).days
            out["days_to_earnings"] = days
            if 0 <= days <= 7:
                out["warning"] = (f"نتائج الشركة بعد {days} يوم — مخاطرة فتحة سعرية "
                                  "مفاجئة تلغي أي ستوب ضيق")
        except Exception:
            pass
    return out


def bundle(symbol: str, meta: dict | None = None) -> dict:
    """Everything non-price the analysis is allowed to lean on."""
    meta = meta or {}
    return {
        "headlines": headlines(symbol),
        "social": social(symbol),
        "events": event_risk(meta),
    }
