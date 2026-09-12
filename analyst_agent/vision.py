"""What the screenshot itself says.

The picture is used for identification only — which asset, which timeframe,
what the user drew on it — never for measuring levels. Pixels have no numbers
behind them; every price in the final answer comes from downloaded bars.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import frames, groq_client, symbols

log = logging.getLogger(__name__)

SYSTEM = (
    "You are a precise chart-screenshot reader. You extract only what is "
    "literally visible in the image. You never estimate values that are not "
    "printed, and you never analyse the market. Reply with JSON only."
)

PROMPT = """Read this trading chart screenshot and return JSON with exactly these keys:

{
  "is_chart": true/false,
  "symbol": "ticker exactly as printed, e.g. AAPL or TADAWUL:2222 or 2222 or BTCUSDT, else null",
  "company_name": "any company/asset name printed, else null",
  "exchange": "exchange or platform printed (NASDAQ, TADAWUL, BINANCE, TradingView...), else null",
  "timeframe": "timeframe exactly as printed, e.g. 15, M15, 1H, 4h, 1D, D, W, else null",
  "last_price": "the last/current price printed, as a number, else null",
  "currency": "currency symbol or code if printed, else null",
  "indicators_visible": ["names of indicators shown, e.g. RSI, MACD, EMA 50, Volume"],
  "user_drawings": ["what the user drew: trendline, support zone, fibonacci, arrow, text note..."],
  "drawn_prices": ["any price numbers the user typed or highlighted on the chart"],
  "chart_style": "candles | bars | line | heikin-ashi | unknown",
  "visible_date_range": "date range printed on the x axis, else null",
  "notes": "one short sentence on anything unusual (log scale, after-hours, pre-market, halted...)"
}

Rules: copy text exactly as printed, do not translate ticker text, do not guess
a symbol that is not visible (use null), and do not analyse or predict anything.
Output JSON only, no prose."""


@dataclass
class ChartRead:
    is_chart: bool = False
    symbol_raw: str | None = None
    symbol: str | None = None
    company_name: str | None = None
    exchange: str | None = None
    timeframe_raw: str | None = None
    frame_key: str | None = None
    last_price: float | None = None
    indicators: list[str] = field(default_factory=list)
    drawings: list[str] = field(default_factory=list)
    drawn_prices: list[str] = field(default_factory=list)
    chart_style: str | None = None
    notes: str | None = None
    error: str | None = None
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "is_chart": self.is_chart, "symbol_in_image": self.symbol_raw,
            "resolved_symbol": self.symbol, "company_name": self.company_name,
            "exchange": self.exchange, "timeframe_in_image": self.timeframe_raw,
            "frame_from_image": self.frame_key, "last_price_in_image": self.last_price,
            "indicators_in_image": self.indicators, "user_drawings": self.drawings,
            "drawn_prices": self.drawn_prices, "chart_style": self.chart_style,
            "notes": self.notes,
        }


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def read_chart(image: bytes) -> ChartRead:
    """Identify the asset and timeframe printed on the screenshot."""
    try:
        reply = groq_client.vision_chat(PROMPT, image, system=SYSTEM)
    except groq_client.GroqError as exc:
        log.warning("vision read failed: %s", exc)
        return ChartRead(error=str(exc))

    data = groq_client.parse_json(reply)
    if not data:
        return ChartRead(error="تعذّر فهم رد النموذج عن الصورة", raw={"reply": reply[:500]})

    symbol_raw = data.get("symbol") or None
    resolved = symbols.normalize_symbol(symbol_raw)
    if not resolved:
        # Fall back to the printed company name ("Tesla Inc", "تسلا") — but only
        # a real name-table hit: a loose word match would turn "Saudi Aramco"
        # into the ticker "SAUDI".
        for text in (data.get("company_name"), data.get("exchange")):
            named = [c for c in (symbols.resolve(text) if text else [])
                     if c.reason.startswith("name:") or c.reason == "cashtag"]
            if named:
                resolved = named[0].symbol
                break
    # A Saudi-looking numeric code needs the exchange suffix Yahoo expects.
    exchange = (data.get("exchange") or "").upper()
    if resolved and resolved.isdigit() and ("TADAWUL" in exchange or "SAU" in exchange):
        resolved = f"{resolved}.SR"

    # The screenshot's timeframe field may be as terse as "D" or "W".
    frame = frames.parse_label(str(data.get("timeframe") or ""))
    return ChartRead(
        is_chart=bool(data.get("is_chart", True)),
        symbol_raw=symbol_raw,
        symbol=resolved,
        company_name=data.get("company_name"),
        exchange=data.get("exchange"),
        timeframe_raw=str(data.get("timeframe")) if data.get("timeframe") else None,
        frame_key=frame.key if frame else None,
        last_price=_as_float(data.get("last_price")),
        indicators=[str(x) for x in (data.get("indicators_visible") or [])][:12],
        drawings=[str(x) for x in (data.get("user_drawings") or [])][:12],
        drawn_prices=[str(x) for x in (data.get("drawn_prices") or [])][:12],
        chart_style=data.get("chart_style"),
        notes=data.get("notes"),
        raw=data,
    )
