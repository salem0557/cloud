"""Persisted watches and per-period tracking state."""
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field

from . import config
from .periods import PERIODS
from .sources import Asset
from .tracker import Track

log = logging.getLogger(__name__)


@dataclass
class Watch:
    asset: Asset
    periods: set[str] = field(default_factory=set)
    directions: set[str] = field(default_factory=lambda: {"low", "high"})
    muted: bool = False

    def to_dict(self) -> dict:
        return {"asset": self.asset.to_dict(), "periods": sorted(self.periods),
                "directions": sorted(self.directions), "muted": self.muted}

    @classmethod
    def from_dict(cls, d: dict) -> "Watch":
        return cls(asset=Asset.from_dict(d["asset"]),
                   periods={p for p in d.get("periods", []) if p in PERIODS},
                   directions=set(d.get("directions") or ["low", "high"]),
                   muted=bool(d.get("muted")))


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or config.STATE_FILE
        # chat id -> symbol -> Watch
        self.watches: dict[int, dict[str, Watch]] = {}
        # "SYMBOL|period" -> Track (shared across chats: one poll serves all)
        self.tracks: dict[str, Track] = {}
        self.load()

    # ------------------------------------------------------------ lifecycle
    def load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            log.error("state file unreadable (%s); starting fresh", exc)
            return
        for chat_id, items in (data.get("watches") or {}).items():
            self.watches[int(chat_id)] = {
                sym: Watch.from_dict(w) for sym, w in items.items()}
        self.tracks = {k: Track.from_dict(v)
                       for k, v in (data.get("tracks") or {}).items()}

    def save(self) -> None:
        data = {
            "watches": {str(cid): {s: w.to_dict() for s, w in items.items()}
                        for cid, items in self.watches.items()},
            "tracks": {k: t.to_dict() for k, t in self.tracks.items()},
        }
        try:
            directory = os.path.dirname(os.path.abspath(self.path)) or "."
            with tempfile.NamedTemporaryFile("w", dir=directory, delete=False,
                                             encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
                tmp = fh.name
            os.replace(tmp, self.path)   # atomic: never leave a half-written file
        except OSError as exc:
            log.error("could not save state: %s", exc)

    # -------------------------------------------------------------- watches
    def add(self, chat_id: int, asset: Asset, periods=None, directions=None) -> Watch:
        chat = self.watches.setdefault(chat_id, {})
        watch = chat.get(asset.symbol)
        if watch is None:
            watch = Watch(asset=asset,
                          periods=set(periods or config.DEFAULT_PERIODS),
                          directions=set(directions or config.DEFAULT_DIRECTIONS))
            chat[asset.symbol] = watch
        else:
            if periods:
                watch.periods |= set(periods)
            if directions:
                watch.directions |= set(directions)
        return watch

    def remove(self, chat_id: int, symbol: str) -> bool:
        chat = self.watches.get(chat_id) or {}
        removed = chat.pop(symbol, None) is not None
        self.prune_tracks()
        return removed

    def get(self, chat_id: int, symbol: str) -> Watch | None:
        return (self.watches.get(chat_id) or {}).get(symbol)

    def list_watches(self, chat_id: int) -> list[Watch]:
        return list((self.watches.get(chat_id) or {}).values())

    def count(self, chat_id: int) -> int:
        return len(self.watches.get(chat_id) or {})

    # --------------------------------------------------------------- polling
    def active(self) -> dict[str, tuple[Asset, set[str]]]:
        """Every symbol anyone is watching, with the union of its periods.

        Polling is per symbol, not per subscriber, so ten chats watching BTC
        cost exactly one request.
        """
        out: dict[str, tuple[Asset, set[str]]] = {}
        for items in self.watches.values():
            for symbol, watch in items.items():
                if watch.muted or not watch.periods:
                    continue
                asset, periods = out.get(symbol, (watch.asset, set()))
                out[symbol] = (asset, periods | watch.periods)
        return out

    def subscribers(self, symbol: str, period: str, kind: str) -> list[int]:
        return [chat_id for chat_id, items in self.watches.items()
                if (w := items.get(symbol)) and not w.muted
                and period in w.periods and kind in w.directions]

    def track(self, symbol: str, period: str) -> Track:
        key = f"{symbol}|{period}"
        if key not in self.tracks:
            self.tracks[key] = Track(symbol=symbol, period=period)
        return self.tracks[key]

    def prune_tracks(self) -> None:
        """Drop tracking state for symbols nobody watches any more."""
        wanted = {f"{sym}|{p}" for sym, (_, periods) in self.active().items()
                  for p in periods}
        for key in [k for k in self.tracks if k not in wanted]:
            del self.tracks[key]
