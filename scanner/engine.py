"""Scan orchestration: universe -> data -> filters -> matches."""
import logging
from dataclasses import dataclass, field

from . import config, data, universe
from .indicators import FILTERS

log = logging.getLogger(__name__)


@dataclass
class Match:
    symbol: str
    price: float
    matched: list[str]                     # filter keys that passed
    details: dict[str, str] = field(default_factory=dict)

    @property
    def score(self) -> int:
        return len(self.matched)

    def signature(self) -> str:
        """Stable identity of the alert, used for change detection."""
        return ",".join(sorted(self.matched))


def evaluate_symbol(symbol: str, df) -> Match | None:
    matched, details = [], {}
    for key, (_, fn) in FILTERS.items():
        try:
            ok, detail = fn(df)
        except Exception:
            log.exception("Filter %s failed on %s", key, symbol)
            ok, detail = False, "خطأ"
        details[key] = detail
        if ok:
            matched.append(key)
    if len(matched) >= config.FILTERS_REQUIRED:
        return Match(symbol, float(df["Close"].iloc[-1]), matched, details)
    return None


def scan_batch(batch: list[str], stats: dict) -> list[Match]:
    """Download and evaluate one batch of symbols, updating `stats` in place.

    Called per batch (instead of one monolithic scan) so the bot can push
    each matching stock to Telegram the moment it is found.
    """
    try:
        frames = data.fetch_batch(batch)
    except Exception:
        log.exception("Batch download failed (%s..)", batch[0])
        stats["errors"] += len(batch)
        return []
    stats["with_data"] += len(frames)
    matches: list[Match] = []
    for sym, df in frames.items():
        if not data.passes_liquidity(df):
            continue
        stats["liquid"] += 1
        m = evaluate_symbol(sym, df)
        if m:
            matches.append(m)
    matches.sort(key=lambda m: (-m.score, m.symbol))
    return matches


def make_batches(symbols: list[str]) -> list[list[str]]:
    return [symbols[i:i + config.BATCH_SIZE]
            for i in range(0, len(symbols), config.BATCH_SIZE)]


def new_stats(total: int) -> dict:
    return {"total": total, "with_data": 0, "liquid": 0, "errors": 0}


def run_scan(symbols: list[str] | None = None) -> tuple[list[Match], dict]:
    """Blocking full scan (kept for scripts/tests); the bot streams batches."""
    if symbols is None:
        symbols = universe.get_universe()
    stats = new_stats(len(symbols))
    matches: list[Match] = []
    for batch in make_batches(symbols):
        matches.extend(scan_batch(batch, stats))
    matches.sort(key=lambda m: (-m.score, m.symbol))
    return matches, stats
