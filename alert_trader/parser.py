"""Turn an alert post into fields. Pure text processing, nothing is executed.

The parser extracts; it does not judge whether an alert is tradable (that is
the validator's job). It never fills a gap with a guess: an expiry without a
year, two different strikes, or a numeric date whose day/month order is unknown
become errors on the result, and the validator rejects the alert.

The message text is data. Nothing in it can reach a command, a setting or a
permission; the only output is a ParsedAlert.
"""
from __future__ import annotations

import datetime as dt
import re

from .models import OptionContract, ParsedAlert

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫", "01234567890123456789.")
_NUM = r"(\d+(?:\.\d+)?|\.\d+)"

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "إبريل": 4,
    "مايو": 5, "يونيو": 6, "يوليو": 7, "أغسطس": 8, "اغسطس": 8, "سبتمبر": 9,
    "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

_EXIT_WORDS = r"(?:خروج|اخرج|أخرج|إغلاق|اغلاق|أغلق|اغلق|بيع|exit|close|sell|stc)"
_UPDATE_WORDS = (r"(?:تعديل|تحديث|رفع الوقف|تحريك الوقف|وقف جديد|هدف جديد|update|"
                 r"move stop|new stop|adjust)")
_ENTRY_WORDS = (r"(?:دخول|ادخل|أدخل|شراء|اشتر|buy|bto|entry|enter|سعر الدخول|"
                r"توصية جديدة|صفقة جديدة|@)")

_TICKER_STOP = {
    "CALL", "CALLS", "PUT", "PUTS", "BUY", "SELL", "BTO", "STC", "TP", "SL", "PT",
    "ID", "ET", "EST", "EDT", "AM", "PM", "DTE", "ITM", "OTM", "ATM", "USD", "NEW",
    "UPDATE", "EXIT", "ENTRY", "ENTER", "STOP", "LOSS", "TARGET", "TARGETS", "C",
    "P", "TO", "AT", "AND", "OR", "THE", "EXP", "EXPIRY", "STRIKE", "PRICE",
    "CLOSE", "OPEN", "LIMIT", "MOVE", "NOW", "HALF", "ALL", "OF", "IN", "ON",
    "TRADE", "ALERT", "OPTION", "OPTIONS", "UPD", "NY", "KSA", "UTC", "MKT", "VS",
    "ADJUST", "PARTIAL", "RISK", "HIGH", "LOW", "SPX", "NDX", "VIX", "OCC",
}

_OCC_RE = re.compile(r"\b(?:O:)?([A-Z]{1,6}\d?\d{6}[CP]\d{8})\b")


def normalize(text: str) -> str:
    text = (text or "").translate(_DIGITS)
    text = re.sub(r"(?<=\d)٬(?=\d{3})", "", text)
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    text = text.replace("：", ":").replace("–", "-").replace("—", "-")
    return re.sub(r"[‎‏‪-‮⁦-⁩]", "", text)


def _f(raw: str) -> float:
    return float(raw)


def _first_pos(pattern: str, text: str) -> int | None:
    m = re.search(pattern, text, re.IGNORECASE)
    return m.start() if m else None


def _kind(text: str) -> str:
    positions = {
        "exit": _first_pos(rf"(?<!\w){_EXIT_WORDS}(?!\w)", text),
        "update": _first_pos(rf"(?<!\w){_UPDATE_WORDS}(?!\w)", text),
        "entry": _first_pos(rf"(?<![\w]){_ENTRY_WORDS}(?!\w)|@", text),
    }
    found = {k: v for k, v in positions.items() if v is not None}
    if not found:
        return "unknown"
    return min(found, key=found.get)


def _ticker(text: str, result: ParsedAlert) -> str | None:
    dollar = {m.upper() for m in re.findall(r"\$([A-Za-z]{1,6})\b", text)}
    if len(dollar) == 1:
        return dollar.pop()
    if len(dollar) > 1:
        result.errors.append(f"أكثر من رمز أصل في التنبيه: {', '.join(sorted(dollar))}")
        return None
    words = {w for w in re.findall(r"(?<![A-Za-z$#])([A-Z]{1,5})(?![A-Za-z])", text)
             if w not in _TICKER_STOP}
    if len(words) == 1:
        return words.pop()
    if len(words) > 1:
        result.errors.append(f"أكثر من رمز محتمل في التنبيه: {', '.join(sorted(words))}")
    return None


def _right(text: str, result: ParsedAlert) -> str | None:
    calls = re.search(r"(?<![A-Za-z])(?:calls?|كول|كال)(?![A-Za-z])|\d[Cc]\b", text, re.I)
    puts = re.search(r"(?<![A-Za-z])(?:puts?|بوت)(?![A-Za-z])|\d[Pp]\b", text, re.I)
    if calls and puts:
        result.errors.append("التنبيه يذكر Call وPut معًا")
        return None
    return "C" if calls else "P" if puts else None


def _strike(text: str, result: ParsedAlert) -> float | None:
    labeled = re.findall(
        rf"(?:strike|سترايك|السترايك|سعر التنفيذ|التنفيذ|تنفيذ)\s*:?\s*\$?{_NUM}", text, re.I)
    if labeled:
        values = {_f(v) for v in labeled}
    else:
        values = {_f(v) for v in re.findall(rf"\$?{_NUM}\s*[CP]\b", text)}
        values |= {_f(v) for v in re.findall(
            rf"\$?{_NUM}\s*(?:calls?|puts?|كول|كال|بوت)(?![A-Za-z])", text, re.I)}
        values |= {_f(v) for v in re.findall(
            rf"(?:calls?|puts?|كول|كال|بوت)\s+\$?{_NUM}(?!\s*[/\-]\d)(?=\s|$)", text, re.I)}
    if len(values) > 1:
        result.errors.append("أكثر من سعر تنفيذ محتمل: " + ", ".join(f"{v:g}" for v in sorted(values)))
        return None
    return values.pop() if values else None


def _year(raw: str) -> int:
    y = int(raw)
    return 2000 + y if y < 100 else y


def _dates(text: str, date_order: str | None, result: ParsedAlert) -> list[dt.date]:
    found: list[dt.date] = []

    def add(y, m, d):
        try:
            found.append(dt.date(y, m, d))
        except ValueError:
            result.errors.append(f"تاريخ غير صالح: {y}-{m}-{d}")

    consumed = text
    for m in re.finditer(r"\b(\d{4})([/\-])(\d{1,2})\2(\d{1,2})\b", text):
        add(int(m.group(1)), int(m.group(3)), int(m.group(4)))
        consumed = consumed.replace(m.group(0), " ")
    for m in re.finditer(r"\b(\d{1,2})([/\-])(\d{1,2})\2(\d{2}|\d{4})\b", consumed):
        a, b, y = int(m.group(1)), int(m.group(3)), _year(m.group(4))
        if a > 12 and b <= 12:
            add(y, b, a)
        elif b > 12 and a <= 12:
            add(y, a, b)
        elif a == b:
            add(y, a, b)
        elif date_order == "MDY":
            add(y, a, b)
        elif date_order == "DMY":
            add(y, b, a)
        else:
            result.errors.append(
                f"تاريخ ملتبس {m.group(0)}: لا يُعرف هل هو شهر/يوم أم يوم/شهر (حدد date_order)")
        consumed = consumed.replace(m.group(0), " ")
    for m in re.finditer(rf"\b(\d{{1,2}})\s*({_MONTH_ALT})\.?,?\s*(\d{{4}})\b", consumed, re.I):
        add(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
        consumed = consumed.replace(m.group(0), " ")
    for m in re.finditer(rf"\b({_MONTH_ALT})\.?\s*(\d{{1,2}}),?\s*(\d{{4}})\b", consumed, re.I):
        add(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
        consumed = consumed.replace(m.group(0), " ")
    yearless = (re.search(r"(?<![\d.])(\d{1,2})/(\d{1,2})(?![\d/.])", consumed)
                or re.search(rf"\b\d{{1,2}}\s*({_MONTH_ALT})\b|\b({_MONTH_ALT})\.?\s*\d{{1,2}}\b",
                             consumed, re.I))
    if yearless and not found:
        result.errors.append("تاريخ الانتهاء مذكور بلا سنة — لن يُخمَّن")
    return found


def _price_after(keywords: str, text: str) -> list[tuple[float, float | None]]:
    out = []
    pattern = (rf"(?:{keywords})\s*(?:بين|between)?\s*:?\s*\$?{_NUM}"
               rf"(?:\s*(?:-|to|الى|إلى|~|و)\s*\$?{_NUM})?")
    for m in re.finditer(pattern, text, re.I):
        low = _f(m.group(1))
        high = _f(m.group(2)) if m.group(2) else None
        out.append((low, high))
    return out


def _targets(text: str) -> list[float]:
    targets: list[float] = []
    kw = (r"(?:الأهداف|الاهداف|أهداف|اهداف|الهدف|هدف|targets?|tp\d?|pt\d?)")
    for line in text.splitlines():
        hits = list(re.finditer(kw, line, re.I))
        for i, m in enumerate(hits):
            end = hits[i + 1].start() if i + 1 < len(hits) else len(line)
            rest = line[m.end():end]
            rest = re.split(r"(?:وقف|الوقف|ستوب|stop|sl\b|دخول|entry|@)", rest,
                            maxsplit=1, flags=re.I)[0]
            rest = re.sub(r"^\s*(?:الأول|الاول|الثاني|الثالث|الرابع|first|second|third)\s*:?",
                          "", rest, flags=re.I)
            # "هدف 1: 3.2" / "TP 2) 6": drop the index, never a decimal point.
            rest = re.sub(r"^\s*#?\d\s*(?:[:)\-]|\.\s)\s*(?=\$?\.?\d)", "", rest)
            rest = re.sub(r"\(.*?\)", " ", rest)
            for v in re.findall(rf"\$?{_NUM}(?!\s*%)", rest):
                value = _f(v)
                if value not in targets:
                    targets.append(value)
    return targets


def parse(text: str, date_order: str | None = None) -> ParsedAlert:
    text = normalize(text)
    result = ParsedAlert()
    result.kind = _kind(text)

    ref = re.search(r"(?:\bID|رقم الصفقة|رقم التوصية|trade\s*#?)\s*[:#]?\s*([A-Za-z0-9_\-]{1,32})",
                    text, re.I) or re.search(r"#(\d{1,12})\b", text)
    if ref:
        result.alert_ref = ref.group(1)

    occs = {m for m in _OCC_RE.findall(text.upper())}
    if len(occs) > 1:
        result.errors.append("أكثر من رمز عقد في التنبيه")
    elif occs:
        contract = OptionContract.from_occ(occs.pop())
        if contract:
            result.occ_symbol = contract.occ
            result.underlying, result.right = contract.underlying, contract.right
            result.strike, result.expiry = contract.strike, contract.expiry
    else:
        upper_free = _OCC_RE.sub(" ", text)
        result.underlying = _ticker(upper_free, result)
        result.right = _right(upper_free, result)
        result.strike = _strike(upper_free, result)
        labeled = re.search(r"(?:انتهاء|الانتهاء|تاريخ الانتهاء|expiry|expiration|exp)\s*:?\s*([^\n]+)",
                            upper_free, re.I)
        dates = _dates(labeled.group(1), date_order, result) if labeled else []
        if not dates:
            dates = sorted(set(_dates(upper_free, date_order, result)))
        if len(set(dates)) > 1:
            result.errors.append("أكثر من تاريخ في التنبيه دون تحديد أيها الانتهاء")
        elif dates:
            result.expiry = dates[0]
        if result.underlying and result.right and result.strike and result.expiry:
            result.occ_symbol = OptionContract(result.underlying, result.right,
                                               result.strike, result.expiry).occ

    entries = _price_after(r"سعر الدخول|الدخول|دخول|entry|@|شراء|buy|bto|السعر|price", text)
    distinct = {e for e in entries}
    if len(distinct) > 1:
        result.errors.append("أكثر من سعر دخول في التنبيه")
    elif distinct:
        low, high = distinct.pop()
        result.entry_low, result.entry_high = min(low, high or low), max(low, high or low)

    result.targets = _targets(text)
    stops = {_f(v) for v in re.findall(
        rf"(?:وقف الخسارة|وقف خسارة|الوقف|وقف|ستوب|stop\s*loss|stop|sl)\s*(?:جديد|new)?\s*:?\s*(?:عند|at)?\s*\$?{_NUM}",
        text, re.I)}
    if len(stops) > 1:
        result.errors.append("أكثر من وقف خسارة في التنبيه")
    elif stops:
        result.stop = stops.pop()

    if result.kind == "exit":
        pct = re.search(rf"{_EXIT_WORDS}\s*(\d{{1,3}})\s*%", text, re.I)
        if pct:
            result.exit_fraction = min(int(pct.group(1)), 100) / 100
        elif re.search(r"نصف|النصف|half", text, re.I):
            result.exit_fraction = 0.5
        elif re.search(r"ربع|quarter", text, re.I):
            result.exit_fraction = 0.25
        elif re.search(r"جزئي|partial", text, re.I):
            result.errors.append("خروج جزئي دون نسبة محددة")
        if result.exit_fraction == 1:
            result.exit_fraction = None
    result.errors = list(dict.fromkeys(result.errors))
    return result
