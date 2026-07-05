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
    options_text: str = ""                 # best-contracts block, filled at send time
    sentiment_text: str = ""               # news+social summary, filled at send time
    # Transient (never persisted/compared): the OHLCV history used to evaluate
    # this symbol, kept only long enough to render the alert's chart image.
    chart_df: object = field(default=None, repr=False, compare=False)
    chart_png: object = field(default=None, repr=False, compare=False)

    @property
    def score(self) -> int:
        return len(self.matched)

    @property
    def total_filters(self) -> int:
        return len(FILTERS)

    def signature(self) -> str:
        """Stable identity of the alert, used for change detection."""
        return ",".join(sorted(self.matched))


def evaluate_symbol(symbol: str, df) -> Match:
    """Evaluate `symbol` against the filter set; the caller decides against
    FILTERS_REQUIRED whether it qualifies."""
    price = float(df["Close"].iloc[-1])
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
    return Match(symbol, price, matched, details=details, chart_df=df)


@dataclass
class BatchResult:
    matches: list          # Match objects with score >= FILTERS_REQUIRED
    hot: list              # symbols with score >= HOTLIST_MIN_SCORE (near-signal)
    liquid: list           # symbols that passed the liquidity filter
    requested: int = 0
    with_data: int = 0

    @property
    def data_ratio(self) -> float:
        """Fraction of requested symbols that returned data; a sudden drop
        means Yahoo is rejecting us and the throttle should kick in."""
        return self.with_data / self.requested if self.requested else 1.0


def scan_batch(batch: list[str], stats: dict) -> BatchResult:
    """Download and evaluate one batch of symbols, updating `stats` in place.

    Called per batch (instead of one monolithic scan) so the bot can push
    each matching stock to Telegram the moment it is found.
    """
    result = BatchResult([], [], [], requested=len(batch))
    try:
        frames = data.fetch_batch(batch)
    except Exception:
        log.exception("Batch download failed (%s..)", batch[0])
        stats["errors"] += len(batch)
        return result
    result.with_data = len(frames)
    stats["with_data"] += len(frames)
    for sym, df in frames.items():
        if not data.passes_liquidity(df):
            continue
        stats["liquid"] += 1
        result.liquid.append(sym)
        m = evaluate_symbol(sym, df)
        if m.score >= config.FILTERS_REQUIRED:
            result.matches.append(m)
        if m.score >= config.HOTLIST_MIN_SCORE:
            result.hot.append(sym)
    result.matches.sort(key=lambda m: (-m.score, m.symbol))
    return result


def scan_batch_task(batch: list[str]) -> tuple[BatchResult, dict]:
    """Self-contained batch scan for a worker process: pandas/yfinance memory
    accumulates in the parent otherwise (container OOM'd around 950MB), so
    batches run in a recycled subprocess that gives memory back to the OS."""
    stats = new_stats(len(batch))
    result = scan_batch(batch, stats)
    return result, stats


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
        matches.extend(scan_batch(batch, stats).matches)
    matches.sort(key=lambda m: (-m.score, m.symbol))
    return matches, stats
