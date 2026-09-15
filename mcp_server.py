"""MCP server: the analyst engine as tools Claude can call directly.

Runs beside the Telegram bot and exposes the same machinery — the verdict,
the indicators, the journal, the option chain — so a conversation with Claude
reaches the numbers the bot uses instead of re-deriving them.

Run:  python mcp_server.py       (PORT is provided by the host)

Two environment variables matter:
    MCP_TOKEN  a bearer token; without it the URL is open to anyone who finds it
    MCP_PATH   the endpoint path, default /mcp — set an unguessable one for
               clients that cannot send a header
"""
from __future__ import annotations

import logging
import os

import yfinance as yf
from fastmcp import FastMCP

from analyst_agent import analyst, frames, indicators as indicators_mod
from analyst_agent import journal, market, symbols, verdict as verdict_mod
from analyst_agent import watcher

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    level=logging.INFO)
log = logging.getLogger("analyst.mcp")

TOKEN = os.environ.get("MCP_TOKEN", "").strip()
PATH = os.environ.get("MCP_PATH", "/mcp")

auth = None
if TOKEN:
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    auth = StaticTokenVerifier(tokens={TOKEN: {"client_id": "analyst"}})
else:
    log.warning("MCP_TOKEN is not set — this endpoint is open to anyone with the URL")

mcp = FastMCP("salem-analyst", auth=auth)


@mcp.tool()
def analyze(symbol: str, frame: str = "1d", question: str = "") -> dict:
    """تحليل كامل لرمز على فريم: الاتجاه، الثقة، الدخول، الستوب، الأهداف،
    النطاق المتوقع، والتحذيرات. نفس المحرك الذي يستخدمه بوت تيليجرام."""
    request = " ".join(part for part in (symbol, frame, question) if part)
    answer = analyst.analyze(request, with_news=True)
    return {
        "ok": answer.ok,
        "symbol": answer.symbol,
        "frame": answer.frame_key,
        "headline": answer.headline,
        "text": answer.text,
        "debug": answer.debug,
    }


@mcp.tool()
def snapshot(symbol: str, frame: str = "1d") -> dict:
    """كل الأرقام الخام لرمز على فريم: المتوسطات، RSI، MACD، ADX، ATR،
    بولنجر، الفوليوم، الدعوم والمقاومات، فيبوناتشي، نماذج الشموع."""
    frame_obj = frames.get(frame)
    candidates = symbols.resolve(symbol) or [symbol]
    data = market.load(candidates, frame_obj)
    if data is None:
        return {"error": f"لا تتوفر بيانات لـ {symbol} على فريم {frame_obj.label_ar}"}
    facts = indicators_mod.analyze(data.df, data.frame.key, daily_df=data.daily_df)
    facts["frame_label"] = data.frame.label_ar
    call = verdict_mod.decide(facts)
    return {"symbol": data.symbol, "frame": data.frame.key,
            "live_price": data.live_price, "technicals": facts,
            "verdict": call.to_dict()}


@mcp.tool()
def expirations(symbol: str) -> list:
    """تواريخ انتهاء عقود الخيارات المتاحة لرمز."""
    try:
        return list(yf.Ticker(symbol).options)
    except Exception as exc:
        return [f"error: {exc}"]


@mcp.tool()
def option_chain(symbol: str, expiration: str, side: str = "calls",
                 limit: int = 40) -> list:
    """سلسلة الخيارات: سترايك، عرض، طلب، آخر سعر، حجم، فائدة مفتوحة، تقلب ضمني.
    side: calls أو puts."""
    try:
        chain = yf.Ticker(symbol).option_chain(expiration)
    except Exception as exc:
        return [{"error": str(exc)}]
    table = chain.puts if side.lower().startswith("p") else chain.calls
    columns = ["strike", "bid", "ask", "lastPrice", "volume",
               "openInterest", "impliedVolatility"]
    available = [c for c in columns if c in table.columns]
    return table[available].head(max(1, min(limit, 200))).to_dict(orient="records")


@mcp.tool()
def performance(source: str = "") -> str:
    """نسبة الإصابة الفعلية من سجل التوصيات، مقابل النسبة التي يحتاجها
    العائد/المخاطرة للتعادل. source: ask (بالطلب) أو auto (تلقائية)."""
    journal.evaluate()
    return journal.stats(source or None)


@mcp.tool()
def scan(symbols_list: list[str] | None = None, frame: str = "") -> list:
    """مسح قائمة المراقبة بشروط التوصيات التلقائية، بلا نشر أي شيء.
    يرجع لكل رمز: الاتجاه، الثقة، R:R، وسبب القبول أو الرفض."""
    rows = watcher.evaluate_watchlist(symbols_list or None, frame or None)
    return [{"symbol": r.symbol, "passes": r.ok, "reason": r.reason or "يحقق الشروط",
             "side": r.side, "conviction": r.conviction, "score": r.score,
             "rr": r.rr, "adx": r.adx, "relative_volume": r.rel_volume,
             "price": r.price} for r in rows]


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    log.info("MCP server on %s (auth: %s)", PATH, "token" if TOKEN else "OPEN")
    mcp.run(transport="http", host="0.0.0.0", port=port, path=PATH)
