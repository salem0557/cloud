"""أدوات مساعدة عامة مشتركة بين main.py والوحدات الثلاث -- تنسيق نصوص فقط،
بلا أي منطق فحص أو فلترة (ذاك في indicators.py/كل وحدة على حدة)."""

MSG_LIMIT = 3800  # keep below Telegram's 4096-char cap


def fmt_price(p: float) -> str:
    """189.20$ for stocks, 61,250.00$ for BTC, 0.000012$ for micro-cap coins."""
    if p >= 1:
        return f"{p:,.2f}$"
    return f"{p:.6f}".rstrip("0").rstrip(".") + "$"


def fmt_volume(v: float) -> str:
    """1.23B$ / 45.6M$ / 8.9K$ / 120$."""
    if v >= 1e9:
        return f"{v / 1e9:.2f}B$"
    if v >= 1e6:
        return f"{v / 1e6:.1f}M$"
    if v >= 1e3:
        return f"{v / 1e3:.1f}K$"
    return f"{v:.0f}$"


def split_message(text: str) -> list[str]:
    """Split a long results block into Telegram-sized chunks on paragraph
    boundaries (each result is one blank-line-separated block)."""
    parts = text.split("\n\n")
    chunks, current = [], parts[0]
    for part in parts[1:]:
        if len(current) + len(part) + 2 > MSG_LIMIT:
            chunks.append(current)
            current = part
        else:
            current += "\n\n" + part
    chunks.append(current)
    return chunks
