"""Extreme tracking and break detection — the core of the bot, kept pure.

One Track per (symbol, period). It holds the lowest and highest price seen so
far inside the current period; when the live price beats one of them, that is
a new period extreme and worth a message.

Two guards keep a symbol grinding steadily downward from sending an alert per
poll: the new price must beat the *last announced* price by MIN_MOVE_PCT (so
small steps accumulate instead of being lost), and a cooldown floors the gap
between two alerts of the same kind.
"""
import time
from dataclasses import asdict, dataclass, field


@dataclass
class Track:
    symbol: str
    period: str
    key: str = ""              # period_key it was built for; a change = rollover
    low: float = 0.0
    high: float = 0.0
    low_ts: float = 0.0
    high_ts: float = 0.0
    # Per direction ("low"/"high"): what we last told the user, and when.
    alert_price: dict = field(default_factory=dict)
    alert_ts: dict = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return bool(self.key) and self.low > 0 and self.high > 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Track":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class Break:
    symbol: str
    period: str
    kind: str          # "low" | "high"
    price: float
    previous: float    # the extreme this one replaced
    pct: float         # distance from that previous extreme, signed
    ts: float


def reset(track: Track, key: str, low: float, high: float,
          now: float | None = None) -> Track:
    """Start a fresh period from candle-derived extremes.

    Called on the first sighting and at every rollover, which is also what
    makes a restart or an outage harmless: the period is rebuilt from real
    candles rather than from whatever the bot happened to see while running.
    """
    now = now if now is not None else time.time()
    track.key = key
    track.low, track.high = low, high
    track.low_ts = track.high_ts = now
    track.alert_price = {}
    track.alert_ts = {}
    return track


def check(track: Track, price: float, min_move_pct: float, cooldown_s: float,
          now: float | None = None) -> Break | None:
    """Record `price` and return a Break when it is an announceable extreme.

    The stored extreme always follows the price — only the *announcement* is
    rate-limited, so the bot never reports a low that is no longer the low.
    """
    now = now if now is not None else time.time()
    if price <= 0 or not track.ready:
        return None

    if price < track.low:
        previous, kind = track.low, "low"
        track.low, track.low_ts = price, now
    elif price > track.high:
        previous, kind = track.high, "high"
        track.high, track.high_ts = price, now
    else:
        return None

    last_price = track.alert_price.get(kind)
    if last_price:
        # Measured against the last announced price, not against the extreme
        # we just replaced: otherwise a slow drift of sub-threshold steps
        # would never clear the bar and would never be reported at all.
        moved = abs(price - last_price) / last_price * 100
        if moved < min_move_pct:
            return None
        if (price >= last_price) if kind == "low" else (price <= last_price):
            return None

    last_ts = track.alert_ts.get(kind, 0.0)
    if now - last_ts < cooldown_s:
        return None

    track.alert_price[kind] = price
    track.alert_ts[kind] = now
    pct = ((price - previous) / previous * 100) if previous else 0.0
    return Break(symbol=track.symbol, period=track.period, kind=kind,
                 price=price, previous=previous, pct=pct, ts=now)
