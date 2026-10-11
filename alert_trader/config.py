"""Two sources of configuration, kept apart on purpose.

* Environment variables: secrets and connection details (tokens, ids, URLs).
  Never written to the database, the logs or the settings file.
* The trading settings file (JSON): every risk and execution rule. The owner
  fills it once; the bot never edits a risk value on its own.

A settings file with "demo": true may drive paper trading only. Live trading
refuses to start unless "demo" is false and every value in LIVE_REQUIRED is set.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:      # optional in tests
    pass


def _data_dir() -> Path:
    path = Path(os.getenv("ALERT_DATA_DIR")
                or os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
                or os.getenv("DATA_DIR")
                or "./data")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _int(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    return int(raw) if raw.lstrip("-").isdigit() else None


@dataclass
class Env:
    bot_token: str | None
    owner_id: int | None
    channel_id: int | None
    source_author: str | None       # optional extra filter (signature / via-bot)
    transport: str                  # botapi | userbot
    tg_api_id: int | None
    tg_api_hash: str | None
    tg_session: str | None
    abyan_mcp_url: str | None
    abyan_mcp_token: str | None
    abyan_portfolio_id: int | None
    quote_provider: str             # cboe | polygon | tradier
    polygon_api_key: str | None
    tradier_token: str | None
    tradier_base_url: str
    tradier_delayed: bool
    live_armed: bool                # second key for live mode, outside the settings file
    data_dir: Path
    settings_path: Path

    @classmethod
    def load(cls) -> "Env":
        data_dir = _data_dir()
        return cls(
            bot_token=os.getenv("ALERT_BOT_TOKEN") or None,
            owner_id=_int("OWNER_TELEGRAM_ID"),
            channel_id=_int("SOURCE_CHANNEL_ID"),
            source_author=(os.getenv("SOURCE_AUTHOR") or "").strip() or None,
            transport=(os.getenv("CHANNEL_TRANSPORT") or "botapi").strip().lower(),
            tg_api_id=_int("TG_API_ID"),
            tg_api_hash=os.getenv("TG_API_HASH") or None,
            tg_session=os.getenv("TG_SESSION") or None,
            abyan_mcp_url=os.getenv("ABYAN_MCP_URL") or None,
            abyan_mcp_token=os.getenv("ABYAN_MCP_TOKEN") or None,
            abyan_portfolio_id=_int("ABYAN_PORTFOLIO_ID"),
            quote_provider=(os.getenv("QUOTE_PROVIDER") or "cboe").strip().lower(),
            polygon_api_key=os.getenv("POLYGON_API_KEY") or None,
            tradier_token=os.getenv("TRADIER_TOKEN") or None,
            tradier_base_url=os.getenv("TRADIER_BASE_URL") or "https://api.tradier.com",
            tradier_delayed=(os.getenv("TRADIER_DELAYED", "1") != "0"),
            live_armed=os.getenv("LIVE_TRADING_ARMED") == "1",
            data_dir=data_dir,
            settings_path=Path(os.getenv("TRADING_SETTINGS_PATH")
                               or data_dir / "trading_settings.json"),
        )


# Values the owner must decide before live trading. None means "not set".
LIVE_REQUIRED = (
    "live_capital_usd", "max_cost_per_trade_usd", "max_total_exposure_usd",
    "max_open_positions", "daily_loss_limit_usd", "daily_loss_method",
    "max_alert_age_sec", "max_entry_deviation_pct", "max_spread_pct",
    "max_quote_age_sec", "allowed_symbols", "min_dte", "max_dte", "allow_0dte",
    "exit_without_source", "close_before_expiry_minutes", "targets_split",
    "abyan_options_enabled_confirmed",
)
PAPER_REQUIRED = tuple(k for k in LIVE_REQUIRED
                       if k not in ("live_capital_usd",
                                    "abyan_options_enabled_confirmed")) + (
    "paper_capital_usd",)


@dataclass
class Settings:
    demo: bool = True
    mode: str = "paper"                      # paper | live
    paper_capital_usd: float | None = None
    live_capital_usd: float | None = None
    max_cost_per_trade_usd: float | None = None
    max_total_exposure_usd: float | None = None
    max_open_positions: int | None = None
    daily_loss_limit_usd: float | None = None
    daily_loss_method: str | None = None     # realized | realized_plus_unrealized
    max_alert_age_sec: float | None = None
    max_entry_deviation_pct: float | None = None
    max_spread_pct: float | None = None
    max_quote_age_sec: float | None = None
    allow_delayed_quotes: bool = False
    min_open_interest: int = 0
    min_volume: int = 0
    allowed_symbols: list[str] | None = None  # ["*"] means any symbol
    min_dte: int | None = None
    max_dte: int | None = None
    allow_0dte: bool | None = None
    require_stop: bool = False
    date_order: str | None = None            # MDY | DMY: how the source writes 11/05/2026
    exit_without_source: dict | None = None  # see example file
    close_before_expiry_minutes: int | None = None
    targets_split: list[float] | None = None
    move_stop_to_entry_after_first_target: bool = False
    entry_only_regular_session: bool = True
    duplicate_window_sec: int = 600
    entry_order_ttl_sec: int = 60
    exit_order_ttl_sec: int = 30
    exit_max_reprices: int = 3
    slippage_ticks: int = 1
    simulated_latency_sec: float = 2.0
    require_quote_size_for_fill: bool = True
    tick_below_3: float = 0.01
    tick_above_3: float = 0.05
    fmv_tolerance_pct: float = 15.0
    paper_allow_without_broker_verification: bool = False
    abyan_options_enabled_confirmed: bool | None = None
    fees: dict = field(default_factory=lambda: {
        # Abyan connector documentation, 50%-discount (first month) rates, USD.
        "per_contract": 0.495, "min_per_order": 0.995,
        "low_premium_threshold": 0.10, "low_premium_per_contract": 0.245,
        "low_premium_min_per_order": 0.495,
        "vat_pct": 15.0, "third_party_per_contract": 0.0,
    })
    extra_holidays: list[str] = field(default_factory=list)
    extra_early_closes: list[str] = field(default_factory=list)
    monitor_interval_sec: int = 15
    notes: str = ""

    @classmethod
    def from_file(cls, path: Path) -> "Settings":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        unknown = sorted(set(data) - set(known) - {"_comment", "_labels"})
        if unknown:
            raise ValueError(f"إعدادات غير معروفة: {', '.join(unknown)}")
        return cls(**known)

    def missing(self, required) -> list[str]:
        return [k for k in required if getattr(self, k) in (None, "", [])]

    def problems(self) -> list[str]:
        """Reasons this settings file cannot drive its declared mode."""
        out = []
        if self.mode not in ("paper", "live"):
            out.append(f"mode غير معروف: {self.mode}")
        if self.daily_loss_method not in (None, "realized", "realized_plus_unrealized"):
            out.append("daily_loss_method يجب أن يكون realized أو realized_plus_unrealized")
        if self.date_order not in (None, "MDY", "DMY"):
            out.append("date_order يجب أن يكون MDY أو DMY")
        if self.targets_split and abs(sum(self.targets_split) - 1) > 1e-6:
            out.append("مجموع targets_split يجب أن يساوي 1")
        missing = self.missing(PAPER_REQUIRED)
        if missing:
            out.append("قيم ناقصة: " + ", ".join(missing))
        if self.mode == "live":
            if self.demo:
                out.append("التشغيل الحقيقي يرفض ملف إعدادات تجريبي (demo=true)")
            missing = self.missing(LIVE_REQUIRED)
            if missing:
                out.append("قيم ناقصة للتشغيل الحقيقي: " + ", ".join(missing))
            if self.abyan_options_enabled_confirmed is not True:
                out.append("لم تؤكد أن حساب أبيان مفعّل لتداول الخيارات")
        return out

    def symbol_allowed(self, symbol: str) -> bool:
        allowed = [s.upper() for s in (self.allowed_symbols or [])]
        return "*" in allowed or symbol.upper() in allowed

    def tick(self, price: float) -> float:
        return self.tick_below_3 if price < 3 else self.tick_above_3
