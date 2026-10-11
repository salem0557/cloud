import datetime as dt
import json

import pytest

from alert_trader.broker.abyan import LiveTradingBlocked
from alert_trader.engine import Engine
from alert_trader.execution import LiveExecutor, PaperExecutor
from alert_trader.market_calendar import NY, UTC
from alert_trader.models import OptionContract

from .conftest import AAPL, ENTRY, World, make_settings


def enter(w, bid=2.50, ask=2.55, size=10):
    w.quote(bid, ask, size, size)
    status, mid = w.post(ENTRY)
    assert status == "accepted", w.store.q("SELECT reasons FROM alerts")[-1]["reasons"]
    w.clock.advance(2)
    w.quote(bid, ask, size, size)
    w.engine.tick()
    return mid


def cash(w):
    c, r = w.store.paper_cash()
    return round(c, 2), round(r, 2)


def test_accept_and_fill_at_ask_plus_slippage(world):
    enter(world)
    t = world.trade()
    assert t["status"] == "open"
    assert t["qty_filled"] == 3                      # 1000$ cap / (2.56 * 100)
    assert t["avg_entry"] == pytest.approx(2.56)     # ask 2.55 + 1 tick, capped by limit 2.56
    assert t["broker_verified"] == 1
    c, reserved = cash(world)
    assert reserved == 0
    assert c == pytest.approx(10_000 - 3 * 256 - t["entry_fees"], abs=0.01)
    texts = world.texts()
    joined = "\n".join(texts)
    assert "أُرسل أمر شراء" in joined and "تم تنفيذ شراء" in joined
    assert texts and all(x.startswith("🧪 [ورقي]") for x in texts)


def test_accept_message_is_not_a_fill(world):
    world.quote(2.50, 2.55)
    world.post(ENTRY)
    texts = world.texts()
    assert any("بانتظار التنفيذ" in t for t in texts)
    assert not any("تم تنفيذ" in t for t in texts)


def test_no_fill_on_quote_older_than_order(world):
    world.quote(2.50, 2.55)
    world.post(ENTRY)
    world.clock.advance(5)
    world.engine.tick()          # same quote, stamped before the order reached the market
    assert world.trade()["qty_filled"] == 0


def test_missing_year_rejected_with_reason(world):
    world.quote(2.50, 2.55)
    status, _ = world.post("شراء كول AAPL 230 انتهاء 11/20 دخول 2.5 وقف 1.9")
    assert status == "rejected"
    reasons = json.loads(world.store.q("SELECT reasons FROM alerts")[-1]["reasons"])
    assert any("بلا سنة" in r for r in reasons)


def test_duplicate_delivery_and_repost(world):
    world.quote(2.50, 2.55)
    status, mid = world.post(ENTRY)
    assert status == "accepted"
    assert world.post(ENTRY, message_id=mid)[0] == "duplicate_delivery"
    assert world.post(ENTRY)[0] == "duplicate"          # new message, same alert ID/contract
    assert world.store.one("SELECT COUNT(*) n FROM trades")["n"] == 1


def test_edit_after_entry_never_buys_again(world):
    mid = enter(world)
    status, _ = world.post(ENTRY.replace("1.90", "2.10"), message_id=mid, edit=True)
    assert status == "edit_of_trade"
    assert world.store.one("SELECT COUNT(*) n FROM trades")["n"] == 1
    assert world.trade()["stop"] == 2.10
    other = ENTRY.replace("سترايك: 230", "سترايك: 235")
    world.clock.advance(1)
    assert world.post(other, message_id=mid, edit=True)[0] == "edit_of_trade"
    assert world.store.one("SELECT COUNT(*) n FROM trades")["n"] == 1
    assert world.store.one("SELECT COUNT(*) n FROM orders WHERE side='buy'")["n"] == 1


def test_edit_fixes_rejected_alert(world):
    world.quote(2.50, 2.55)
    bad = ENTRY.replace("2026-11-20", "11/20")
    status, mid = world.post(bad)
    assert status == "rejected"
    world.clock.advance(5)
    assert world.post(ENTRY, message_id=mid, edit=True)[0] == "accepted"


def test_edit_cannot_revive_old_alert(world):
    world.quote(2.50, 2.55)
    status, mid = world.post(ENTRY.replace("2026-11-20", "11/20"))
    world.clock.advance(600)
    world.quote(2.50, 2.55)
    assert world.post(ENTRY, message_id=mid, edit=True,
                      date=world.clock() - dt.timedelta(seconds=600))[0] == "rejected"


@pytest.mark.parametrize("change,needle", [
    (dict(bid=2.50, ask=2.55, age=120), "عمر السعر"),
    (dict(bid=2.00, ask=2.55), "فارق العرض"),
    (dict(bid=3.00, ask=3.05), "انحراف"),
    (dict(bid=2.50, ask=2.55, delayed=True), "متأخرة"),
    (dict(bid=2.50, ask=2.55, ask_size=0), "حجم الطلب"),
])
def test_price_checks(world, change, needle):
    world.quote(**change)
    assert world.post(ENTRY)[0] == "rejected"
    reasons = world.store.q("SELECT reasons FROM alerts")[-1]["reasons"]
    assert needle in reasons


def test_contract_not_listed_at_broker(world):
    world.quote(2.50, 2.55, contract=OptionContract("AAPL", "C", 235.0, dt.date(2026, 11, 20)))
    assert world.post(ENTRY.replace("سترايك: 230", "سترايك: 235"))[0] == "rejected"
    assert "لا يوجد في أبيان" in world.store.q("SELECT reasons FROM alerts")[-1]["reasons"]


def test_broker_down_rejects(world):
    world.broker.down = True
    world.quote(2.50, 2.55)
    assert world.post(ENTRY)[0] == "rejected"
    assert "تعذر التحقق من أبيان" in world.store.q("SELECT reasons FROM alerts")[-1]["reasons"]


def test_no_broker_needs_explicit_paper_permission(tmp_path):
    w = World(tmp_path, broker=False)
    w.quote(2.50, 2.55)
    assert w.post(ENTRY)[0] == "rejected"
    (tmp_path / "b").mkdir()
    w2 = World(tmp_path / "b", settings=make_settings(paper_allow_without_broker_verification=True),
               broker=False)
    w2.quote(2.50, 2.55)
    assert w2.post(ENTRY)[0] == "accepted"
    assert w2.trade()["broker_verified"] == 0


def test_stale_alert_rejected(world):
    world.quote(2.50, 2.55)
    status, _ = world.post(ENTRY, date=world.clock() - dt.timedelta(seconds=600))
    assert status == "rejected"


def test_foreign_chat_ignored(world):
    world.quote(2.50, 2.55)
    assert world.post(ENTRY, chat=-100999)[0] == "ignored_source"
    assert world.store.one("SELECT COUNT(*) n FROM alerts")["n"] == 0


def test_partial_fill_and_reservation(world):
    world.quote(2.50, 2.55, 10, 1)
    world.post(ENTRY)
    for _ in range(3):
        world.clock.advance(2)
        world.quote(2.50, 2.55, 10, 1)
        world.engine.tick()
    t = world.trade()
    assert t["qty_filled"] == 3
    fills = world.store.q("SELECT * FROM fills")
    assert [f["qty"] for f in fills] == [1, 1, 1]
    # commission is per order with a minimum: charged once in total, not per piece
    from alert_trader import fees
    assert sum(f["fees"] for f in fills) == pytest.approx(
        fees.order_fees(world.settings.fees, 3, 2.56), abs=1e-4)
    assert cash(world)[1] == 0
    assert any("تنفيذ جزئي" in t for t in world.texts())


def test_entry_expires_and_releases_cash(world):
    world.quote(2.50, 2.55)
    world.post(ENTRY)
    world.clock.advance(5)
    world.quote(2.70, 2.75)          # ran above the limit
    world.engine.tick()
    world.clock.advance(70)
    world.quote(2.70, 2.75)
    world.engine.tick()
    assert world.trade()["status"] == "canceled"
    assert cash(world) == (10_000, 0)


def test_stop_exit_at_bid_not_at_stop(world):
    enter(world)
    world.clock.advance(5)
    world.quote(1.80, 1.85)        # gapped through the 1.90 stop
    world.engine.tick()
    world.clock.advance(3)
    world.quote(1.80, 1.85)
    world.engine.tick()
    t = world.trade()
    assert t["status"] == "closed"
    assert "وقف الخسارة" in t["exit_reason"]
    sell = world.store.one("SELECT * FROM fills f JOIN orders o ON o.id=f.order_id WHERE o.side='sell'")
    assert sell["price"] == pytest.approx(1.79)    # bid 1.80 - 1 tick, worse than the stop
    assert t["realized_pnl"] < 0
    assert world.engine.daily_pnl() == pytest.approx(t["realized_pnl"], abs=0.01)


def test_targets_partial_exit_policy(world):
    enter(world)
    world.clock.advance(5)
    world.quote(3.25, 3.30)
    world.engine.tick()
    world.clock.advance(3)
    world.quote(3.25, 3.30)
    world.engine.tick()
    t = world.trade()
    assert t["qty_open"] == 2 and t["targets_hit"] == 1     # floor(3 * 0.5) = 1 sold at T1
    world.clock.advance(5)
    world.quote(4.05, 4.10)
    world.engine.tick()
    world.clock.advance(3)
    world.quote(4.05, 4.10)
    world.engine.tick()
    t = world.trade()
    assert t["status"] == "closed" and t["qty_open"] == 0 and t["realized_pnl"] > 0


def test_exit_alert_by_reply_and_partial(world):
    mid = enter(world)
    world.clock.advance(5)
    world.quote(2.80, 2.85)
    status, _ = world.post("بيع 50% من الصفقة", reply_to=mid)
    assert status == "applied"
    world.clock.advance(3)
    world.quote(2.80, 2.85)
    world.engine.tick()
    assert world.trade()["qty_open"] == 2      # floor(3 * 0.5) = 1 closed


def test_exit_alert_unmatched_and_ambiguous(world):
    assert world.post("خروج من AAPL 230 كول 2026-11-20")[0] == "rejected"
    enter(world)
    other = OptionContract("AAPL", "C", 240.0, dt.date(2026, 11, 20))
    world.broker.list_contract(other)
    world.engine.chain._data.clear()
    world.quote(2.50, 2.55, contract=other)
    world.clock.advance(1)
    status, _ = world.post(ENTRY.replace("230", "240").replace("ID: 77", "ID: 78"))
    assert status == "accepted"
    assert world.post("خروج من AAPL كول")[0] == "rejected"
    assert "أكثر من صفقة" in world.store.q("SELECT reasons FROM alerts")[-1]["reasons"]


def test_data_outage_pauses_entries_but_keeps_managing(world):
    enter(world)
    world.quotes.fail = True
    world.tick()
    assert world.engine.entries_paused() and "تعطل" in world.engine.entries_paused()
    world.quotes.fail = False
    world.clock.advance(5)
    world.quote(1.80, 1.85)
    world.engine.tick()
    assert world.engine.entries_paused() is None
    assert world.store.one("SELECT COUNT(*) n FROM orders WHERE side='sell'")["n"] == 1


def test_daily_loss_limit_blocks_entries(world):
    world.settings.daily_loss_limit_usd = 50
    test_stop_exit_at_bid_not_at_stop(world)
    assert "حد الخسارة" in world.engine.entries_paused()


def test_pause_and_close_all_are_separate(world):
    enter(world)
    world.engine.pause("owner")
    world.clock.advance(1)
    other = OptionContract("AAPL", "C", 240.0, dt.date(2026, 11, 20))
    world.broker.list_contract(other)
    world.engine.chain._data.clear()
    world.quote(2.50, 2.55, contract=other)
    assert world.post(ENTRY.replace("230", "240").replace("ID: 77", "ID: 78"))[0] == "rejected"
    assert world.trade()["status"] == "open"            # pause does not close anything
    world.quote(2.50, 2.55)
    assert world.engine.close_all("owner") == 1
    world.clock.advance(3)
    world.quote(2.50, 2.55)
    world.engine.tick()
    assert world.trade()["status"] == "closed"
    assert world.engine.entries_paused()                # close-all does not resume


def test_forced_close_before_expiry(tmp_path):
    w = World(tmp_path, settings=make_settings(allow_0dte=True, min_dte=0))
    exp = OptionContract("AAPL", "C", 230.0, dt.date(2026, 10, 16))
    w.broker.list_contract(exp)
    w.engine.chain._data.clear()
    w.quote(2.50, 2.55, contract=exp)
    w.post(ENTRY.replace("2026-11-20", "2026-10-16"))
    w.clock.advance(2)
    w.quote(2.50, 2.55, contract=exp)
    w.engine.tick()
    w.clock.t = dt.datetime(2026, 10, 16, 15, 31, tzinfo=NY).astimezone(UTC)
    w.quote(2.40, 2.45, contract=exp)
    w.engine.tick()
    w.clock.advance(3)
    w.quote(2.40, 2.45, contract=exp)
    w.engine.tick()
    t = w.trade()
    assert t["status"] == "closed" and "قبل الانتهاء" in t["exit_reason"]


def test_unsold_position_settles_at_expiry(tmp_path):
    w = World(tmp_path)
    enter(w)
    w.clock.t = dt.datetime(2026, 11, 20, 16, 5, tzinfo=NY).astimezone(UTC)
    w.quote(0.0, 0.0, underlying=235.0)
    w.engine.tick()
    t = w.trade()
    assert t["status"] == "expired" and "يُمرَّن" in t["exit_reason"]


def test_restart_resumes_without_rebuying(tmp_path):
    w = World(tmp_path)
    w.quote(2.50, 2.55)
    status, mid = w.post(ENTRY)
    e2 = Engine(w.settings, w.env, w.store, w.quotes, w.broker, PaperExecutor(w.settings),
                clock=w.clock)
    e2.start()
    w.engine = e2
    assert w.post(ENTRY, message_id=mid)[0] == "duplicate_delivery"
    w.clock.advance(2)
    w.quote(2.50, 2.55)
    e2.tick()
    assert w.trade()["qty_filled"] == 3
    assert w.store.one("SELECT COUNT(*) n FROM orders WHERE side='buy'")["n"] == 1
    assert w.store.one("SELECT COUNT(*) n FROM ledger WHERE kind='deposit'")["n"] == 1


def test_paper_mode_never_touches_broker_writes(world):
    enter(world)
    world.engine.close_all("x")
    world.tick()
    assert not {"place_order", "cancel_order", "replace_order"} & set(world.broker.calls)


def test_live_executor_refuses_in_paper(world):
    with pytest.raises(LiveTradingBlocked):
        LiveExecutor(world.settings, world.env, world.broker, guard=lambda: None)
    live = make_settings(mode="live", demo=False, live_capital_usd=1000,
                         abyan_options_enabled_confirmed=True)
    with pytest.raises(LiveTradingBlocked, match="LIVE_TRADING_ARMED"):
        LiveExecutor(live, world.env, world.broker, guard=lambda: None)
    world.env.live_armed = True
    with pytest.raises(LiveTradingBlocked, match="للكتابة"):
        LiveExecutor(live, world.env, world.broker, guard=lambda: None)   # read-only broker
    world.broker.read_only = False
    ex = LiveExecutor(live, world.env, world.broker, guard=lambda: "الوضع تغيّر إلى ورقي")
    with pytest.raises(LiveTradingBlocked):
        ex.submit({"occ": AAPL.occ, "side": "buy", "qty": 1, "limit_price": 2.5})
    assert world.broker.placed == []


def test_live_unknown_order_is_reconciled_not_resent(tmp_path):
    from alert_trader.broker.fake import FakeBroker
    from alert_trader.broker.abyan import BrokerUnavailable
    live = make_settings(mode="live", demo=False, live_capital_usd=1000,
                         abyan_options_enabled_confirmed=True)
    w = World(tmp_path, settings=live)
    w.env.live_armed = True
    broker = w.broker
    broker.read_only = False

    def drop(*a, **k):
        broker.calls.append("place_order")
        raise BrokerUnavailable("timeout")
    broker.place_limit = drop
    w.engine = Engine(live, w.env, w.store, w.quotes, broker,
                      LiveExecutor(live, w.env, broker, guard=lambda: None), clock=w.clock)
    w.quote(2.50, 2.55)
    assert w.post(ENTRY)[0] == "accepted"
    order = w.store.one("SELECT * FROM orders")
    assert order["status"] == "unknown"
    broker.order_rows.append({
        "client_order_id": "abc", "symbol": AAPL.occ, "side_raw": "buy", "qty": order["qty"],
        "limit_price": str(order["limit_price"]), "status_raw": "filled",
        "filled_qty": order["qty"], "filled_avg_price": 2.55, "commission": "1.5", "vat": "0.22",
        "created_at": (w.clock() + dt.timedelta(seconds=1)).isoformat()})
    w.clock.advance(5)
    w.engine.tick()
    order = w.store.one("SELECT * FROM orders")
    assert order["status"] == "filled" and order["broker_order_id"] == "abc"
    assert broker.calls.count("place_order") == 1


def test_reports_render(world):
    enter(world)
    world.post("رسالة عامة بلا تنبيه")
    from alert_trader import reports
    day = reports.daily(world.engine)
    week = reports.weekly(world.engine)
    assert "ورقي" in day and "التقرير اليومي" in day
    assert "جلسات السوق المشمولة" in week and "لم تُختبر" in week
    assert "التنفيذ الحقيقي" in week or "الأوامر الحقيقية" in week


def test_example_settings_are_paper_only():
    from pathlib import Path
    from alert_trader.config import Settings
    s = Settings.from_file(Path(__file__).parents[1] / "trading_settings.example.json")
    assert s.demo and s.mode == "paper" and s.problems() == []
    s.mode = "live"
    problems = " ".join(s.problems())
    assert "demo" in problems and "live_capital_usd" in problems


def test_ui_builds_and_notifier_dedupes(world):
    import asyncio
    from alert_trader.ui import Notifier, build_app
    world.env.bot_token = "123:abc"
    app, panel = build_app(world.env, world.engine)
    assert panel.notifier is not None

    class Bot:
        sent = []

        async def send_message(self, chat_id, text, **kw):
            self.sent.append(text)
    n = Notifier(Bot(), 1)
    asyncio.run(n.send([("a", "x"), ("a", "x"), ("data_outage", "y"), ("data_outage", "y")]))
    assert Bot.sent == ["x", "y"]
