"""Plain data shapes shared by every layer."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

_OCC = re.compile(r"^(?:O:)?([A-Z]{1,6}\d?)(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


@dataclass(frozen=True)
class OptionContract:
    underlying: str
    right: str          # "C" or "P"
    strike: float
    expiry: dt.date
    root: str | None = None   # OCC root when it differs from the underlying

    @property
    def occ(self) -> str:
        root = self.root or self.underlying
        return (f"{root}{self.expiry:%y%m%d}{self.right}"
                f"{int(round(self.strike * 1000)):08d}")

    @property
    def right_name(self) -> str:
        return "call" if self.right == "C" else "put"

    def label(self) -> str:
        kind = "CALL" if self.right == "C" else "PUT"
        return f"{self.underlying} {self.strike:g} {kind} {self.expiry.isoformat()}"

    @classmethod
    def from_occ(cls, symbol: str) -> "OptionContract | None":
        m = _OCC.match(symbol.strip().upper())
        if not m:
            return None
        root, yy, mm, dd, right, strike = m.groups()
        try:
            expiry = dt.date(2000 + int(yy), int(mm), int(dd))
        except ValueError:
            return None
        underlying = root.rstrip("0123456789")
        return cls(underlying, right, int(strike) / 1000, expiry,
                   root if root != underlying else None)


@dataclass
class RawMessage:
    """One channel post as it reached us, from either transport."""
    chat_id: int
    message_id: int
    date: dt.datetime                 # original post time (UTC)
    text: str
    edit_date: dt.datetime | None = None
    reply_to_message_id: int | None = None
    author_signature: str | None = None
    via_bot: str | None = None
    sender_id: int | None = None
    transport: str = "botapi"
    received_at: dt.datetime = field(
        default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    @property
    def version(self) -> int:
        return int(self.edit_date.timestamp()) if self.edit_date else 0


@dataclass
class ParsedAlert:
    kind: str = "unknown"            # entry | update | exit | unknown
    underlying: str | None = None
    right: str | None = None
    strike: float | None = None
    expiry: dt.date | None = None
    occ_symbol: str | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    targets: list[float] = field(default_factory=list)
    stop: float | None = None
    alert_ref: str | None = None
    exit_fraction: float | None = None   # 0.5 for "close half"; None = all
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def contract(self) -> OptionContract | None:
        if self.occ_symbol:
            return OptionContract.from_occ(self.occ_symbol)
        if self.underlying and self.right and self.strike and self.expiry:
            return OptionContract(self.underlying, self.right, self.strike, self.expiry)
        return None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind, "underlying": self.underlying, "right": self.right,
            "strike": self.strike,
            "expiry": self.expiry.isoformat() if self.expiry else None,
            "occ_symbol": self.occ_symbol, "entry_low": self.entry_low,
            "entry_high": self.entry_high, "targets": self.targets,
            "stop": self.stop, "alert_ref": self.alert_ref,
            "exit_fraction": self.exit_fraction, "errors": self.errors,
            "notes": self.notes,
        }


@dataclass
class Quote:
    symbol: str
    bid: float | None
    ask: float | None
    quote_time: dt.datetime | None      # when the market showed these prices
    source: str
    delayed: bool = False
    bid_size: int | None = None
    ask_size: int | None = None
    last: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    underlying_price: float | None = None
    fetched_at: dt.datetime = field(
        default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2

    def spread_pct(self) -> float | None:
        mid = self.mid
        if not mid:
            return None
        return (self.ask - self.bid) / mid * 100

    def age_sec(self, now: dt.datetime) -> float | None:
        if self.quote_time is None:
            return None
        return (now - self.quote_time).total_seconds()

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol, "bid": self.bid, "ask": self.ask,
            "bid_size": self.bid_size, "ask_size": self.ask_size,
            "last": self.last, "volume": self.volume,
            "open_interest": self.open_interest,
            "quote_time": self.quote_time.isoformat() if self.quote_time else None,
            "fetched_at": self.fetched_at.isoformat(),
            "source": self.source, "delayed": self.delayed,
            "underlying_price": self.underlying_price,
        }


# Order lifecycle. "sent" is not "filled": the UI words them differently.
ORDER_NEW = "new"                # recorded locally, not yet handed over
ORDER_SUBMITTED = "submitted"    # handed to the executor, no answer yet
ORDER_ACCEPTED = "accepted"      # executor/broker acknowledged it
ORDER_PARTIAL = "partially_filled"
ORDER_FILLED = "filled"
ORDER_CANCELED = "canceled"
ORDER_REJECTED = "rejected"
ORDER_EXPIRED = "expired"
ORDER_UNKNOWN = "unknown"        # connection dropped after sending: must reconcile

OPEN_ORDER_STATES = {ORDER_NEW, ORDER_SUBMITTED, ORDER_ACCEPTED, ORDER_PARTIAL,
                     ORDER_UNKNOWN}
FINAL_ORDER_STATES = {ORDER_FILLED, ORDER_CANCELED, ORDER_REJECTED, ORDER_EXPIRED}


@dataclass
class OrderUpdate:
    """What an executor reports about one order."""
    status: str
    filled_qty: int = 0              # cumulative
    avg_fill_price: float | None = None
    fees: float = 0.0                # cumulative
    broker_order_id: str | None = None
    reason: str | None = None
    fills: list[dict] = field(default_factory=list)   # new fills since last poll
