import datetime as dt

import pytest

from alert_trader.broker.fake import FakeBroker
from alert_trader.config import Env, Settings
from alert_trader.engine import Engine
from alert_trader.execution import PaperExecutor
from alert_trader.market_calendar import NY, UTC
from alert_trader.models import OptionContract, Quote, RawMessage
from alert_trader.quotes import StaticProvider
from alert_trader.storage import Store

CHANNEL = -1001234567890
AAPL = OptionContract("AAPL", "C", 230.0, dt.date(2026, 11, 20))


class Clock:
    def __init__(self, t: dt.datetime):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds: float):
        self.t += dt.timedelta(seconds=seconds)


def make_settings(**over) -> Settings:
    base = dict(
        demo=True, mode="paper", paper_capital_usd=10_000, max_cost_per_trade_usd=1_000,
        max_total_exposure_usd=3_000, max_open_positions=3, daily_loss_limit_usd=500,
        daily_loss_method="realized_plus_unrealized", max_alert_age_sec=120,
        max_entry_deviation_pct=10, max_spread_pct=10, max_quote_age_sec=30,
        allowed_symbols=["*"], min_dte=0, max_dte=90, allow_0dte=False,
        exit_without_source={"max_hold_minutes": None, "stop_pct": None, "target_pct": None},
        close_before_expiry_minutes=30, targets_split=[0.5, 0.5], date_order="MDY",
        simulated_latency_sec=1, monitor_interval_sec=15,
    )
    base.update(over)
    return Settings(**base)


def env(tmp_path, **over) -> Env:
    base = dict(bot_token=None, owner_id=1, channel_id=CHANNEL, source_author=None,
                transport="botapi", tg_api_id=None, tg_api_hash=None, tg_session=None,
                abyan_mcp_url=None, abyan_mcp_token=None, abyan_portfolio_id=None,
                quote_provider="static", polygon_api_key=None, tradier_token=None,
                tradier_base_url="", tradier_delayed=True, live_armed=False,
                data_dir=tmp_path, settings_path=tmp_path / "s.json")
    base.update(over)
    return Env(**base)


class World:
    def __init__(self, tmp_path, settings=None, broker=True):
        self.clock = Clock(dt.datetime(2026, 10, 12, 10, 30, tzinfo=NY).astimezone(UTC))
        self.settings = settings or make_settings()
        self.env = env(tmp_path)
        self.store = Store(tmp_path / "t.db")
        self.quotes = StaticProvider()
        self.broker = FakeBroker() if broker else None
        if self.broker:
            self.broker.list_contract(AAPL)
            self.broker.fmvs[AAPL.occ] = 2.55
        self.engine = Engine(self.settings, self.env, self.store, self.quotes, self.broker,
                             PaperExecutor(self.settings), clock=self.clock)
        self.engine.start()
        self.next_id = 100

    def quote(self, bid, ask, bid_size=10, ask_size=10, contract=AAPL, age=0, delayed=False,
              underlying=None):
        self.quotes.set(Quote(contract.occ, bid, ask, self.clock() - dt.timedelta(seconds=age),
                              "static", delayed, bid_size, ask_size, underlying_price=underlying))
        self.engine._quote_cache.clear()

    def post(self, text, message_id=None, edit=False, reply_to=None, chat=CHANNEL, date=None):
        if message_id is None:
            self.next_id += 1
            message_id = self.next_id
        msg = RawMessage(chat_id=chat, message_id=message_id,
                         date=date or self.clock() - dt.timedelta(seconds=2), text=text,
                         edit_date=self.clock() if edit else None,
                         reply_to_message_id=reply_to, received_at=self.clock())
        return self.engine.handle_message(msg), message_id

    def tick(self, seconds=5):
        self.clock.advance(seconds)
        self.engine._quote_cache.clear()
        self.engine.tick()

    def trade(self, tid=1):
        return self.store.one("SELECT * FROM trades WHERE id=?", (tid,))

    def texts(self):
        return [t for _, t in self.engine.drain()]


ENTRY = ("🟢 شراء CALL $AAPL\nسترايك: 230\nالانتهاء: 2026-11-20\n"
         "سعر الدخول: 2.50 - 2.60\nالأهداف: 3.20 / 4.00\nوقف الخسارة: 1.90\nID: 77")


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)
