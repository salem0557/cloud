"""Timeframe parsing — the single most important input to the whole read.

The user either writes the frame beside the picture ("حلل لي 15 دقيقة") or the
frame is printed inside the screenshot ("M15", "4H", "1D"). Both spellings land
here and resolve to one canonical `Frame`, which then fixes the data interval,
how much history is pulled, and which higher timeframe is used for context.

Written text always wins over the screenshot: it is the more deliberate of the
two signals.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


@dataclass(frozen=True)
class Frame:
    key: str            # canonical id, also what the user sees
    minutes: int        # bar length in minutes (1mo ~ 30d)
    interval: str       # yfinance interval to download
    period: str         # yfinance period to download
    resample: str | None  # pandas rule when yfinance has no native interval
    label_ar: str
    label_en: str
    context: str | None  # higher timeframe used for trend alignment
    intraday: bool

    def __str__(self) -> str:  # pragma: no cover - debug convenience
        return self.key


# yfinance history limits drive `period`: 1m is capped at 7 days, every other
# sub-hour interval at 60 days, 1h at 730 days.
FRAMES: dict[str, Frame] = {
    "1m":  Frame("1m",  1,     "1m",  "7d",   None, "دقيقة",      "1 min",   "15m", True),
    "2m":  Frame("2m",  2,     "2m",  "20d",  None, "دقيقتين",    "2 min",   "30m", True),
    "3m":  Frame("3m",  3,     "1m",  "7d",   "3min", "3 دقائق",  "3 min",   "30m", True),
    "5m":  Frame("5m",  5,     "5m",  "45d",  None, "5 دقائق",    "5 min",   "1h",  True),
    "10m": Frame("10m", 10,    "5m",  "45d",  "10min", "10 دقائق", "10 min", "1h",  True),
    "15m": Frame("15m", 15,    "15m", "60d",  None, "15 دقيقة",   "15 min",  "1h",  True),
    "30m": Frame("30m", 30,    "30m", "60d",  None, "30 دقيقة",   "30 min",  "4h",  True),
    "45m": Frame("45m", 45,    "15m", "60d",  "45min", "45 دقيقة", "45 min", "4h",  True),
    "1h":  Frame("1h",  60,    "1h",  "270d", None, "ساعة",       "1 hour",  "1d",  True),
    "2h":  Frame("2h",  120,   "1h",  "540d", "2h", "ساعتين",     "2 hour",  "1d",  True),
    "3h":  Frame("3h",  180,   "1h",  "540d", "3h", "3 ساعات",    "3 hour",  "1d",  True),
    "4h":  Frame("4h",  240,   "1h",  "600d", "4h", "4 ساعات",    "4 hour",  "1d",  True),
    "1d":  Frame("1d",  1440,  "1d",  "4y",   None, "يومي",       "Daily",   "1wk", False),
    "1wk": Frame("1wk", 10080, "1wk", "12y",  None, "أسبوعي",     "Weekly",  "1mo", False),
    "1mo": Frame("1mo", 43200, "1mo", "max",  None, "شهري",       "Monthly", None,  False),
}

# Anything a chart platform, a broker app or a person might type.
ALIASES: dict[str, str] = {
    # minutes
    "1": "1m", "m1": "1m", "1min": "1m", "1minute": "1m", "min1": "1m",
    "2": "2m", "m2": "2m", "2min": "2m",
    "3": "3m", "m3": "3m", "3min": "3m",
    "5": "5m", "m5": "5m", "5min": "5m", "5mins": "5m", "5minutes": "5m",
    "10": "10m", "m10": "10m", "10min": "10m",
    "15": "15m", "m15": "15m", "15min": "15m", "15mins": "15m", "quarter": "15m",
    "30": "30m", "m30": "30m", "30min": "30m", "half": "30m",
    "45": "45m", "m45": "45m", "45min": "45m",
    # hours
    "60": "1h", "60m": "1h", "h1": "1h", "1hr": "1h", "1hour": "1h", "hourly": "1h",
    "hour": "1h", "h": "1h",
    "120": "2h", "120m": "2h", "h2": "2h", "2hr": "2h", "2hour": "2h",
    "180": "3h", "180m": "3h", "h3": "3h", "3hr": "3h",
    "240": "4h", "240m": "4h", "h4": "4h", "4hr": "4h", "4hour": "4h", "4hours": "4h",
    # days and up
    "d": "1d", "d1": "1d", "1day": "1d", "day": "1d", "daily": "1d", "1440": "1d",
    "w": "1wk", "w1": "1wk", "1w": "1wk", "1week": "1wk", "week": "1wk",
    "weekly": "1wk", "7d": "1wk",
    "mn": "1mo", "mn1": "1mo", "1month": "1mo", "month": "1mo", "monthly": "1mo",
    "1mn": "1mo", "mo": "1mo", "1m0": "1mo",
}

# Fixed Arabic phrases and digit+unit forms, checked before anything looser.
ARABIC_PATTERNS: list[tuple[str, str]] = [
    # The lookbehind keeps "اربع ساعات" (four hours) out of "ربع ساعة"
    # (quarter hour) — the second is a substring of the first.
    (r"(?<![ء-ي])(?:ال)?ربع\s*ساعه?", "15m"),
    (r"(?<![ء-ي])(?:ال)?نصف?\s*ساعه?", "30m"),
    (r"(?:ال)?شهري|شهر(?:ي)?\b|شهرية", "1mo"),
    (r"(?:ال)?اسبوعي|(?:ال)?أسبوعي|اسبوع\b|أسبوع\b|اسبوعية", "1wk"),
    (r"(?:ال)?يومي|(?:ال)?اليوم\b|يوميه|يومية|يوم\b", "1d"),
    (r"(\d+)\s*(?:دقيقه|دقيقة|دقائق|دقايق|د)\b", "MIN"),
    (r"(\d+)\s*(?:ساعه|ساعة|ساعات|س)\b", "HOUR"),
    (r"(\d+)\s*(?:يوم|ايام|أيام)\b", "DAY"),
]

# "خمس دقايق", "أربع ساعات" — a spelled-out count before the unit. Checked
# after the fixed phrases (so "ربع ساعة" stays 15m) but before the bare units
# (so "خمس دقايق" is not read as the bare word "دقايق" = 1m).
WORD_COUNTS: dict[str, int] = {
    "دقيقتين": 2, "ساعتين": 2, "يومين": 2,
    "ثلاث": 3, "ثلاثة": 3, "اربع": 4, "أربع": 4, "اربعة": 4, "أربعة": 4,
    "خمس": 5, "خمسة": 5, "ست": 6, "سته": 6, "ستة": 6, "سبع": 7, "سبعة": 7,
    "ثمان": 8, "ثمانية": 8, "تسع": 9, "تسعة": 9, "عشر": 10, "عشرة": 10,
    "خمستعشر": 15, "خمسطعش": 15, "عشرين": 20, "ثلاثين": 30, "ثلاثون": 30,
    "اربعين": 40, "خمسين": 50,
}
MINUTE_WORDS = r"(?:دقيقه|دقيقة|دقائق|دقايق)"
HOUR_WORDS = r"(?:ساعه|ساعة|ساعات|سوايع)"

# Last resort: the unit on its own ("على الدقيقة", "فريم الساعة").
BARE_UNIT_PATTERNS: list[tuple[str, str]] = [
    (r"(?:ال)?ساعه|(?:ال)?ساعة|ساعي", "1h"),
    (r"(?:ال)?دقيقه|(?:ال)?دقيقة|(?:ال)?دقايق|(?:ال)?دقائق", "1m"),
]

def normalize(text: str) -> str:
    """Arabic-Indic digits -> ASCII, tatweel and diacritics stripped, lowercased."""
    if not text:
        return ""
    text = text.translate(ARABIC_DIGITS)
    text = re.sub(r"[ؗ-ًؚ-ْـ]", "", text)  # harakat, tatweel
    text = text.replace("‏", " ").replace("‎", " ")
    return text.lower()


def _from_minutes(minutes: int) -> Frame | None:
    """Nearest supported frame at or below an arbitrary minute count."""
    if minutes <= 0:
        return None
    exact = {f.minutes: f for f in FRAMES.values()}
    if minutes in exact:
        return exact[minutes]
    lower = [f for f in FRAMES.values() if f.minutes <= minutes]
    return max(lower, key=lambda f: f.minutes) if lower else FRAMES["1m"]


def get(key: str) -> Frame:
    """Canonical key or alias -> Frame. Unknown input falls back to daily."""
    k = normalize(key).strip()
    if k in FRAMES:
        return FRAMES[k]
    if k in ALIASES:
        return FRAMES[ALIASES[k]]
    parsed = parse(k)
    return parsed or FRAMES["1d"]


def parse(text: str | None) -> Frame | None:
    """First timeframe mentioned in free text, or None if nothing looks like one.

    Handles "15m", "M15", "240", "h4", "4 ساعات", "خمس دقايق", "ربع ساعة",
    "يومي", "daily" — in that order of specificity, so a looser spelling never
    shadows a more exact one.
    """
    if not text:
        return None
    t = normalize(text)

    for pattern, target in ARABIC_PATTERNS:
        m = re.search(pattern, t)
        if not m:
            continue
        if target == "MIN":
            return _from_minutes(int(m.group(1)))
        if target == "HOUR":
            return _from_minutes(int(m.group(1)) * 60)
        if target == "DAY":
            days = int(m.group(1))
            return FRAMES["1d"] if days <= 1 else _from_minutes(days * 1440)
        return FRAMES[target]

    for word, count in WORD_COUNTS.items():
        if re.search(rf"{word}\s*{MINUTE_WORDS}", t) or word == "دقيقتين" and word in t:
            return _from_minutes(count)
        if re.search(rf"{word}\s*{HOUR_WORDS}", t) or word == "ساعتين" and word in t:
            return _from_minutes(count * 60)

    for pattern, target in BARE_UNIT_PATTERNS:
        if re.search(pattern, t):
            return FRAMES[target]

    # "15m", "4h", "1d", "1wk", "1mo" and the M15/H4/D1 broker spellings.
    # Longest suffix first: "1mo" must not be read as "1m" + stray "o".
    for token in re.findall(r"[a-z]{0,2}\d{1,4}\s*(?:mins|min|hrs|hr|mo|wk|m|h|d|w)?"
                            r"|\b(?:mn|mo|m|h|d|w)\d{1,3}\b", t):
        key = token.replace(" ", "")
        if key in FRAMES:
            return FRAMES[key]
        if key in ALIASES:
            return FRAMES[ALIASES[key]]

    # Bare one/two-letter tokens are skipped: "H", "D", "W", "M", "MO" and "MN"
    # are all live tickers, and a ticker must never be read as a timeframe.
    for token in re.findall(r"[a-z]+", t):
        if token in ALIASES and len(token) > 2:
            return FRAMES[ALIASES[token]]
    return None


def parse_label(text: str | None) -> Frame | None:
    """A timeframe *field*, e.g. the "D" or "W" printed in a chart's corner.

    Single letters are accepted here — unlike `parse()`, which sees whole
    sentences where "D" and "W" are far more likely to be tickers.
    """
    if not text:
        return None
    key = normalize(text).strip().replace(" ", "")
    if key in FRAMES:
        return FRAMES[key]
    if key in ALIASES:
        return FRAMES[ALIASES[key]]
    if key == "m":            # TradingView prints "M" for monthly
        return FRAMES["1mo"]
    return parse(text)


def context_frame(frame: Frame) -> Frame | None:
    """The higher timeframe whose trend the read must respect."""
    return FRAMES[frame.context] if frame.context else None


def all_keys() -> list[str]:
    return list(FRAMES)
