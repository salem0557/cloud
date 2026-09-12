"""Ticker resolution: whatever the user (or the screenshot) calls the asset,
turn it into the symbol Yahoo Finance actually serves.

Never guesses silently — `resolve()` returns ranked candidates with a reason,
and `market.load()` confirms a candidate by actually downloading bars, so a
wrong first guess corrects itself instead of producing a read on the wrong
asset.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import config
from .frames import ALIASES as FRAME_ALIASES
from .frames import normalize

# --- Tadawul (Saudi market) -------------------------------------------------
TADAWUL: dict[str, str] = {
    "ارامكو": "2222.SR", "أرامكو": "2222.SR", "aramco": "2222.SR",
    "سابك": "2010.SR", "sabic": "2010.SR",
    "الراجحي": "1120.SR", "راجحي": "1120.SR", "rajhi": "1120.SR",
    "الاهلي": "1180.SR", "الأهلي": "1180.SR", "snb": "1180.SR",
    "الرياض": "1010.SR", "riyad": "1010.SR",
    "البلاد": "1140.SR", "alinma": "1150.SR", "الانماء": "1150.SR",
    "الإنماء": "1150.SR", "سامبا": "1090.SR",
    "معادن": "1211.SR", "maaden": "1211.SR",
    "stc": "7010.SR", "الاتصالات": "7010.SR", "اس تي سي": "7010.SR",
    "موبايلي": "7020.SR", "mobily": "7020.SR", "زين": "7030.SR", "zain": "7030.SR",
    "اكوا": "2082.SR", "اكوا باور": "2082.SR", "acwa": "2082.SR",
    "المراعي": "2280.SR", "almarai": "2280.SR",
    "ينبع": "2290.SR", "ينساب": "2290.SR", "yansab": "2290.SR",
    "سافكو": "2020.SR", "safco": "2020.SR",
    "كيان": "2350.SR", "التصنيع": "2060.SR", "التعدين": "1211.SR",
    "جرير": "4190.SR", "jarir": "4190.SR",
    "العثيم": "4001.SR", "othaim": "4001.SR",
    "اسمنت": "3030.SR", "الاسمنت": "3030.SR",
    "اعمار": "4220.SR", "إعمار": "4220.SR", "emaar": "4220.SR",
    "دار الاركان": "4300.SR", "الاركان": "4300.SR",
    "البحري": "4030.SR", "bahri": "4030.SR",
    "المتقدمة": "2330.SR", "advanced": "2330.SR",
    "علم": "7203.SR", "elm": "7203.SR",
    "سليمان الحبيب": "4013.SR", "الحبيب": "4013.SR",
    "المواساة": "4002.SR", "دله": "4004.SR",
    "التميمي": "4192.SR", "بن داود": "4161.SR",
    "الخليج": "8030.SR", "التعاونية": "8010.SR", "بوبا": "8210.SR",
    "سدك": "4110.SR", "امريكانا": "6015.SR",
    "تداول": "1111.SR", "السوق المالية": "1111.SR",
}

SAUDI_INDEX = {"تاسي": "^TASI.SR", "تاسى": "^TASI.SR", "المؤشر": "^TASI.SR",
               "مؤشر تداول": "^TASI.SR", "tasi": "^TASI.SR"}

# --- US names people write in Arabic ---------------------------------------
US_NAMES: dict[str, str] = {
    "ابل": "AAPL", "آبل": "AAPL", "أبل": "AAPL", "apple": "AAPL",
    "تسلا": "TSLA", "تيسلا": "TSLA", "tesla": "TSLA",
    "انفيديا": "NVDA", "نفيديا": "NVDA", "إنفيديا": "NVDA", "nvidia": "NVDA",
    "امازون": "AMZN", "أمازون": "AMZN", "amazon": "AMZN",
    "مايكروسوفت": "MSFT", "microsoft": "MSFT",
    "جوجل": "GOOGL", "قوقل": "GOOGL", "google": "GOOGL", "الفابت": "GOOGL",
    "ميتا": "META", "فيسبوك": "META", "facebook": "META",
    "نتفلكس": "NFLX", "نتفليكس": "NFLX", "netflix": "NFLX",
    "بالانتير": "PLTR", "بالنتير": "PLTR", "palantir": "PLTR",
    "امد": "AMD", "ايه ام دي": "AMD",
    "انتل": "INTC", "إنتل": "INTC", "intel": "INTC",
    "بوينج": "BA", "بوينغ": "BA", "boeing": "BA",
    "علي بابا": "BABA", "alibaba": "BABA",
    "سوفي": "SOFI", "ريفيان": "RIVN", "لوسد": "LCID", "لوسيد": "LCID",
    "اوبر": "UBER", "أوبر": "UBER", "uber": "UBER",
    "مايكرون": "MU", "كوالكوم": "QCOM", "برودكوم": "AVGO",
    "كوين بيز": "COIN", "كوينبيز": "COIN", "coinbase": "COIN",
    "مايكرو ستراتيجي": "MSTR", "ستراتيجي": "MSTR",
    "سنابشات": "SNAP", "سناب": "SNAP", "سوبر مايكرو": "SMCI",
    "ارم": "ARM", "سيسكو": "CSCO", "ديزني": "DIS", "نايك": "NKE",
    "ستاربكس": "SBUX", "ماكدونالدز": "MCD", "كوكا": "KO", "بيبسي": "PEP",
    "فايزر": "PFE", "مودرنا": "MRNA", "جونسون": "JNJ",
    "فيزا": "V", "ماستركارد": "MA", "بايبال": "PYPL",
    "جي بي مورقان": "JPM", "قولدمان": "GS", "باركشير": "BRK-B",
    "فورد": "F", "جنرال موتورز": "GM", "نيو": "NIO", "شي بي دي"  : "XPEV",
}

INDICES: dict[str, str] = {
    "spx": "^GSPC", "sp500": "^GSPC", "s&p": "^GSPC", "s&p500": "^GSPC",
    "spy": "SPY", "اس اند بي": "^GSPC", "ستاندرد": "^GSPC",
    "ndx": "^NDX", "nasdaq": "^IXIC", "نازداك": "^IXIC", "ناسداك": "^IXIC",
    "qqq": "QQQ", "dow": "^DJI", "داو": "^DJI", "دوجونز": "^DJI",
    "dji": "^DJI", "vix": "^VIX", "الخوف": "^VIX", "russell": "^RUT",
    "dax": "^GDAXI", "نيكي": "^N225", "nikkei": "^N225", "ftse": "^FTSE",
}

COMMODITIES: dict[str, str] = {
    "ذهب": "GC=F", "الذهب": "GC=F", "gold": "GC=F", "xauusd": "GC=F",
    "xau": "GC=F", "فضه": "SI=F", "فضة": "SI=F", "silver": "SI=F",
    "xagusd": "SI=F", "نفط": "CL=F", "النفط": "CL=F", "oil": "CL=F",
    "wti": "CL=F", "برنت": "BZ=F", "brent": "BZ=F", "غاز": "NG=F",
    "nat gas": "NG=F", "نحاس": "HG=F", "copper": "HG=F",
    "قمح": "ZW=F", "بلاتين": "PL=F", "platinum": "PL=F",
}

CRYPTO: dict[str, str] = {
    "btc": "BTC-USD", "bitcoin": "BTC-USD", "بتكوين": "BTC-USD",
    "بيتكوين": "BTC-USD", "btcusdt": "BTC-USD", "btcusd": "BTC-USD",
    "eth": "ETH-USD", "ethereum": "ETH-USD", "ايثيريوم": "ETH-USD",
    "إيثيريوم": "ETH-USD", "ethusdt": "ETH-USD",
    "sol": "SOL-USD", "solana": "SOL-USD", "سولانا": "SOL-USD",
    "xrp": "XRP-USD", "ripple": "XRP-USD", "ريبل": "XRP-USD",
    "doge": "DOGE-USD", "دوج": "DOGE-USD", "ada": "ADA-USD",
    "bnb": "BNB-USD", "ton": "TON11419-USD", "avax": "AVAX-USD",
    "link": "LINK-USD", "matic": "MATIC-USD", "shib": "SHIB-USD",
    "trx": "TRX-USD", "dot": "DOT-USD", "ltc": "LTC-USD",
    "bch": "BCH-USD", "xlm": "XLM-USD", "atom": "ATOM-USD", "uni": "UNI-USD",
    "etc": "ETC-USD", "near": "NEAR-USD", "apt": "APT-USD", "arb": "ARB-USD",
    "op": "OP-USD", "sui": "SUI-USD", "fil": "FIL-USD", "icp": "ICP-USD",
    "algo": "ALGO-USD", "vet": "VET-USD", "aave": "AAVE-USD", "mkr": "MKR-USD",
    "inj": "INJ-USD", "tia": "TIA-USD", "sei": "SEI-USD", "stx": "STX-USD",
    "imx": "IMX-USD", "rune": "RUNE-USD", "egld": "EGLD-USD", "ftm": "FTM-USD",
    "sand": "SAND-USD", "mana": "MANA-USD", "axs": "AXS-USD", "gala": "GALA-USD",
    "chz": "CHZ-USD", "crv": "CRV-USD", "ldo": "LDO-USD", "grt": "GRT-USD",
    "snx": "SNX-USD", "hbar": "HBAR-USD", "kas": "KAS-USD", "tao": "TAO-USD",
    "rndr": "RNDR-USD", "fet": "FET-USD", "wld": "WLD-USD", "jup": "JUP-USD",
    "pyth": "PYTH-USD", "pepe": "PEPE24478-USD", "bonk": "BONK-USD",
    # Arabic spellings people actually type
    "ايثر": "ETH-USD", "ايثيريم": "ETH-USD", "سولانا": "SOL-USD",
    "كاردانو": "ADA-USD", "بولكادوت": "DOT-USD", "تشين لينك": "LINK-USD",
    "لايتكوين": "LTC-USD", "بينانس كوين": "BNB-USD", "شيبا": "SHIB-USD",
    "دوجكوين": "DOGE-USD", "ترون": "TRX-USD", "افالانش": "AVAX-USD",
    "بيبي": "PEPE24478-USD", "كريبتو": "BTC-USD",
}

FX_CODES = {"usd", "eur", "gbp", "jpy", "chf", "aud", "nzd", "cad", "sar",
            "aed", "try", "cny", "kwd", "egp", "qar", "bhd", "omr", "jod"}

# Words that look like tickers but never are.
STOPWORDS = {
    "ta", "rsi", "macd", "ema", "sma", "bb", "atr", "vwap", "fib", "tf",
    "ok", "pls", "plz", "buy", "sell", "long", "short", "call", "put",
    "usd", "chart", "and", "the", "for", "now", "new", "all", "am", "pm",
    "hi", "hey", "yes", "no", "what", "how", "why", "when", "who", "eod",
    "ath", "atl", "ipo", "ceo", "cfo", "eps", "pe", "usa", "us", "eu",
    "ai", "gpt", "api", "tp", "sl", "be", "dm", "pm", "bro", "man", "lol",
}


@dataclass(frozen=True)
class Candidate:
    symbol: str
    kind: str      # us | tadawul | crypto | fx | commodity | index
    reason: str    # how it was recognised, shown in debug/logs
    confidence: float


# Arabic punctuation sits inside the same Unicode block as Arabic letters, so
# a trailing "؟" used to glue itself to the name and break every word-boundary
# lookaround ("نفيديا؟" matched nothing).
ARABIC_PUNCT = re.compile(r"[؟،؛٪٫٬؍!]")


def _clean(text: str) -> str:
    t = normalize(text)
    t = ARABIC_PUNCT.sub(" ", t)
    return re.sub(r"[^\w؀-ۿ$^&\.\-= ]+", " ", t)


def _tables(all_markets: bool = False) -> list[tuple[dict, str, float]]:
    """Name tables consulted for this deployment, in confidence order."""
    tables: list[tuple[dict, str, float]] = [(US_NAMES, "us", 0.92),
                                             (INDICES, "index", 0.88)]
    if all_markets or not config.US_ONLY:
        tables = [(TADAWUL, "tadawul", 0.93), (SAUDI_INDEX, "index", 0.9)] + tables
    if all_markets or config.ALLOW_NON_EQUITY:
        tables += [(CRYPTO, "crypto", 0.9), (COMMODITIES, "commodity", 0.9)]
    return tables


def _lookup_names(t: str, all_markets: bool = False) -> list[Candidate]:
    out: list[Candidate] = []
    tables = _tables(all_markets)
    for table, kind, conf in tables:
        for name, symbol in table.items():
            name_n = normalize(name)
            pattern = rf"(?<![\w؀-ۿ]){re.escape(name_n)}(?![\w؀-ۿ])"
            if re.search(pattern, t):
                out.append(Candidate(symbol, kind, f"name:{name}", conf + len(name_n) / 1000))
    return out


def _numeric_is_frame(t: str, token: str) -> bool:
    """A 4-digit token that is really a timeframe ("فريم 1440", "240m")."""
    if token not in FRAME_ALIASES:
        return False
    return bool(re.search(rf"(?:فريم|frame|tf|interval|على)\s*{token}\b", t)
                or re.search(rf"\b{token}\s*(?:m|min|h|دق|دقيقه|دقيقة|دقائق)", t))


def resolve(text: str | None, all_markets: bool = False) -> list[Candidate]:
    """Ranked symbol candidates found in free text (best first).

    `all_markets=True` ignores this deployment's market limits — used only to
    tell the user "that symbol is outside the market I am set up for" instead
    of the unhelpful "I could not find a symbol".
    """
    if not text:
        return []
    t = _clean(text)
    out: list[Candidate] = []

    # 1. $TICKER is unambiguous.
    for m in re.finditer(r"\$([a-z]{1,6})(?![\w])", t):
        out.append(Candidate(m.group(1).upper(), "us", "cashtag", 0.99))

    # 2. Yahoo-style symbols written out in full: 2222.SR, BTC-USD, GC=F, ^GSPC.
    for m in re.finditer(r"(?<![\w])(\^?[a-z0-9]{1,8}(?:\.sr|\.ta|\.l|\.de|\.pa)"
                         r"|\^[a-z0-9]{2,6}|[a-z]{2,6}-usd|[a-z]{1,3}=[fx])(?![\w])", t):
            out.append(Candidate(m.group(1).upper(), "explicit", "yahoo-form", 0.97))

    # 3. Arabic/English asset names.
    out.extend(_lookup_names(t, all_markets))

    # 4. Tadawul 4-digit codes (skipped in US-only mode: "2222" is not a
    #    US ticker, and reading it as one would answer on the wrong asset).
    if all_markets or (not config.US_ONLY and config.ASSUME_TADAWUL_FOR_DIGITS):
        for m in re.finditer(r"(?<![\w.])(\d{4})(?![\w.])", t):
            token = m.group(1)
            if _numeric_is_frame(t, token):
                continue
            out.append(Candidate(f"{token}.SR", "tadawul", "tadawul-code", 0.9))

    # 5. FX pairs: EURUSD, usd/jpy.
    if all_markets or config.ALLOW_NON_EQUITY:
        for m in re.finditer(r"(?<![\w])([a-z]{3})\s*/?\s*([a-z]{3})(?![\w])", t):
            a, b = m.group(1), m.group(2)
            if a in FX_CODES and b in FX_CODES and a != b:
                out.append(Candidate(f"{a}{b}=X".upper(), "fx", "fx-pair", 0.9))

    # 6. Bare upper-case-ish tickers, lowest confidence.
    for m in re.finditer(r"(?<![\w$])([a-z]{1,5})(?![\w])", t):
        token = m.group(1)
        if token in STOPWORDS or token in FRAME_ALIASES or len(token) < 2:
            continue
        if re.search(rf"(?<![\w$]){token}(?![\w])", _clean(text)) and token.isalpha():
            out.append(Candidate(token.upper(), "us", "bare-token", 0.45))

    # De-duplicate, keeping the best reason for each symbol, and drop anything
    # this deployment does not cover.
    best: dict[str, Candidate] = {}
    for c in out:
        if not all_markets and not serves(c.symbol):
            continue
        if c.symbol not in best or c.confidence > best[c.symbol].confidence:
            best[c.symbol] = c
    return sorted(best.values(), key=lambda c: -c.confidence)


def serves(symbol: str) -> bool:
    """Is this symbol inside the market this deployment answers for?"""
    upper = symbol.upper()
    if config.US_ONLY and (upper.endswith(".SR") or "TASI" in upper):
        return False
    if not config.ALLOW_NON_EQUITY and (upper.endswith("-USD") or upper.endswith("=X")
                                        or upper.endswith("=F")):
        return False
    return True


def normalize_symbol(raw: str | None) -> str | None:
    """A symbol a vision model read off the screenshot -> Yahoo form.

    "TADAWUL:2222" -> 2222.SR, "BINANCE:BTCUSDT" -> BTC-USD, "XAUUSD" -> GC=F.
    """
    if not raw:
        return None
    s = raw.strip().upper().replace(" ", "")
    if not s or s in ("N/A", "NONE", "UNKNOWN", "NULL", "?"):
        return None
    if ":" in s:                      # exchange prefix from TradingView
        exchange, s = s.split(":", 1)
        if exchange in ("TADAWUL", "SAU", "SASE") and s.isdigit():
            return None if config.US_ONLY else f"{s}.SR"
    low = s.lower()
    for table in (CRYPTO, COMMODITIES, INDICES, TADAWUL, SAUDI_INDEX, US_NAMES):
        if low in {k.lower() for k in table}:
            return {k.lower(): v for k, v in table.items()}[low]
    if s.endswith("USDT") or s.endswith("PERP"):
        base = s.replace("PERP", "").replace("USDT", "")
        return f"{base}-USD"
    if s.isdigit() and len(s) == 4:
        return None if config.US_ONLY else f"{s}.SR"
    if re.fullmatch(r"[A-Z]{6}", s) and s[:3].lower() in FX_CODES and s[3:].lower() in FX_CODES:
        return f"{s}=X"
    if re.fullmatch(r"[A-Z0-9\.\-\^=]{1,12}", s):
        return s if serves(s) else None
    return None


def is_saudi(symbol: str) -> bool:
    return symbol.upper().endswith(".SR")


def currency_of(symbol: str) -> str:
    if is_saudi(symbol):
        return "ريال"
    if symbol.upper().endswith("=X"):
        return ""
    return "$"
