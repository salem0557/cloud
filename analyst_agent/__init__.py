"""Chart-analyst agent: a Telegram userbot that reads a chart screenshot,
rebuilds it from real market data with indicators, and answers with a
decisive Arabic technical read.

Layers (each usable on its own):
    frames      timeframe parsing (Arabic/English/MT/TradingView spellings)
    symbols     ticker resolution (US, Tadawul, crypto, metals, FX)
    market      OHLCV download per timeframe
    indicators  every number the read is built from
    verdict     deterministic direction/entry/stop/targets from those numbers
    chart        annotated PNG
    vision      what the screenshot itself says (Groq vision)
    news        headlines, social chatter, earnings date
    analyst     orchestrates all of the above into one answer
    userbot     Telethon glue for groups and DMs
"""
