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


def run_scan(symbols: list[str] | None = None,
             progress_cb=None) -> tuple[list[Match], dict]:
    """Scan the universe. Returns (matches, stats).

    progress_cb, if given, is called as progress_cb(done, total) after each batch.
    """
    if symbols is None:
        symbols = universe.get_universe()
    stats = {"total": len(symbols), "with_data": 0, "liquid": 0, "errors": 0}
    matches: list[Match] = []

    for start in range(0, len(symbols), config.BATCH_SIZE):
        batch = symbols[start:start + config.BATCH_SIZE]
        try:
            frames = data.fetch_batch(batch)
        except Exception:
            log.exception("Batch download failed (%s..)", batch[0])
            stats["errors"] += len(batch)
            continue
        stats["with_data"] += len(frames)
        for sym, df in frames.items():
            if not data.passes_liquidity(df):
                continue
            stats["liquid"] += 1
            m = evaluate_symbol(sym, df)
            if m:
                matches.append(m)
        if progress_cb:
            progress_cb(min(start + config.BATCH_SIZE, len(symbols)), len(symbols))

    matches.sort(key=lambda m: (-m.score, m.symbol))
    return matches, stats
