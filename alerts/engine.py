"""The polling cycle: one price request per symbol, fanned out to every chat
watching it.
"""
import logging
import time

from . import config, sources, tracker
from .periods import LABELS, now_in, period_key
from .sources import Asset, fmt_price
from .store import Store
from .tracker import Break

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, store: Store | None = None, source=sources):
        self.store = store or Store()
        self.source = source          # injectable for tests
        self.last_poll: float = 0.0
        self.last_error: str = ""
        self.errors: dict[str, int] = {}   # symbol -> consecutive failures

    def poll(self) -> list[tuple[int, Break, Asset]]:
        """One cycle. Returns (chat_id, break, asset) for every alert to send."""
        out: list[tuple[int, Break, Asset]] = []
        active = self.store.active()
        if not active:
            return out

        for symbol, (asset, periods) in active.items():
            if (asset.market == "stock" and config.STOCK_HOURS_ONLY
                    and not self.source.stock_market_open()):
                continue
            try:
                price = self.source.price(asset)
            except Exception as exc:
                self._note_error(symbol, exc)
                continue
            self.errors.pop(symbol, None)

            for period in sorted(periods):
                brk = self._check_period(asset, period, price)
                if brk is None:
                    continue
                for chat_id in self.store.subscribers(symbol, period, brk.kind):
                    out.append((chat_id, brk, asset))

        self.last_poll = time.time()
        self.store.save()
        return out

    def _check_period(self, asset: Asset, period: str, price: float) -> Break | None:
        track = self.store.track(asset.symbol, period)
        key = period_key(now_in(asset.market), period)
        if track.key != key or not track.ready:
            # New period (or first sighting): rebuild the extremes from candles
            # so an alert reflects the whole period, not just the part of it
            # the bot happened to be running for.
            try:
                low, high = self.source.extremes(asset, period)
            except Exception as exc:
                self._note_error(f"{asset.symbol}:{period}", exc)
                return None
            tracker.reset(track, key, min(low, price), max(high, price))
            return None
        return tracker.check(track, price, config.MIN_MOVE_PCT,
                             config.COOLDOWN_MINUTES * 60)

    def _note_error(self, key: str, exc: Exception) -> None:
        self.errors[key] = self.errors.get(key, 0) + 1
        self.last_error = f"{key}: {exc}"
        # One failed request is normal (rate limit, blip); a run of them is not.
        if self.errors[key] in (1, 10, 50):
            log.warning("poll failed for %s (%d in a row): %s",
                        key, self.errors[key], exc)


def format_break(brk: Break, asset: Asset) -> str:
    """The alert message itself."""
    if brk.kind == "low":
        head, arrow = "🔻 أدنى سعر", "هبوط"
    else:
        head, arrow = "🚀 أعلى سعر", "صعود"
    kind_word = "قاع" if brk.kind == "low" else "قمة"
    return (f"{head} {LABELS[brk.period]}\n"
            f"<b>{asset.display}</b> — <b>{fmt_price(brk.price)}$</b>\n"
            f"كسر {kind_word} {LABELS[brk.period]} السابق "
            f"({fmt_price(brk.previous)}$) بنسبة {abs(brk.pct):.2f}% {arrow}")
