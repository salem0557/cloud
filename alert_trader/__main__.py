"""Entry point.

    python -m alert_trader run              # the bot (default)
    python -m alert_trader check            # configuration + connectivity doctor
    python -m alert_trader probe --minutes 15 [--which botapi|userbot|both]
    python -m alert_trader login            # create TG_SESSION for the userbot transport
    python -m alert_trader replay FILE      # feed sample alerts through the parser + checks, offline
    python -m alert_trader report daily|weekly
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import sys

from . import market_calendar as cal
from . import quotes as quotes_mod
from . import reports
from .broker import abyan
from .broker.abyan import LiveTradingBlocked
from .config import Env, Settings
from .engine import Engine
from .execution import LiveExecutor, PaperExecutor
from .storage import Store

log = logging.getLogger("alert_trader")


def load() -> tuple[Env, Settings]:
    env = Env.load()
    if not env.settings_path.exists():
        sys.exit(f"ملف الإعدادات غير موجود: {env.settings_path}\n"
                 "انسخ alert_trader/trading_settings.example.json إليه وعدّل القيم.")
    return env, Settings.from_file(env.settings_path)


def make_engine(env: Env, settings: Settings) -> Engine:
    problems = settings.problems()
    if problems:
        sys.exit("الإعدادات غير صالحة:\n- " + "\n- ".join(problems))
    store = Store(env.data_dir / "alert_trader.db")
    quotes = quotes_mod.build(env)
    live = settings.mode == "live"
    broker = abyan.build(env, read_only=not live)
    if live:
        def guard() -> str | None:
            # Re-read the file on every write: switching it back to paper stops live orders at once.
            try:
                current = Settings.from_file(env.settings_path)
            except Exception as e:
                return f"تعذر قراءة الإعدادات: {e}"
            if current.mode != "live":
                return "ملف الإعدادات لم يعد في الوضع الحقيقي"
            if not Env.load().live_armed:
                return "LIVE_TRADING_ARMED لم يعد مفعّلًا"
            return None
        executor = LiveExecutor(settings, env, broker, guard)
    else:
        executor = PaperExecutor(settings)
    return Engine(settings, env, store, quotes, broker, executor)


async def _run_async(env, engine) -> None:
    from .channel import run_userbot
    from .ui import build_app
    app, panel = build_app(env, engine)
    async with app:
        await app.start()
        allowed = ["message", "callback_query", "channel_post", "edited_channel_post"]
        await app.updater.start_polling(allowed_updates=allowed, drop_pending_updates=False)
        await panel.flush()
        tasks = []
        if env.transport == "userbot":
            tasks.append(asyncio.create_task(run_userbot(env, panel.on_raw)))
        try:
            await asyncio.gather(*tasks) if tasks else await asyncio.Event().wait()
        finally:
            await app.updater.stop()
            await app.stop()


def cmd_run(args) -> None:
    env, settings = load()
    for name, value in (("ALERT_BOT_TOKEN", env.bot_token), ("OWNER_TELEGRAM_ID", env.owner_id),
                        ("SOURCE_CHANNEL_ID", env.channel_id)):
        if not value:
            sys.exit(f"{name} غير معيّن")
    engine = make_engine(env, settings)
    engine.start()
    log.info("mode=%s transport=%s quotes=%s abyan=%s", engine.mode, env.transport,
             engine.quotes.name, "configured" if engine.broker else "not configured")
    asyncio.run(_run_async(env, engine))


def cmd_check(args) -> None:
    env, settings = load()
    print("متغيرات البيئة (يظهر وجودها فقط، لا قيمها):")
    for name, value in (("ALERT_BOT_TOKEN", env.bot_token), ("OWNER_TELEGRAM_ID", env.owner_id),
                        ("SOURCE_CHANNEL_ID", env.channel_id), ("CHANNEL_TRANSPORT", env.transport),
                        ("TG_API_ID/HASH/SESSION", env.tg_api_id and env.tg_api_hash and env.tg_session),
                        ("ABYAN_MCP_URL", env.abyan_mcp_url), ("ABYAN_MCP_TOKEN", env.abyan_mcp_token),
                        ("QUOTE_PROVIDER", env.quote_provider), ("LIVE_TRADING_ARMED", env.live_armed)):
        print(f"  {'✓' if value else '✗'} {name}")
    problems = settings.problems()
    print(f"\nالإعدادات ({settings.mode}{', demo' if settings.demo else ''}):",
          "سليمة" if not problems else "\n  - " + "\n  - ".join(problems))
    broker = abyan.build(env, read_only=True)
    if broker is None:
        print("\n✗ أبيان: ABYAN_MCP_URL غير معيّن — لا تحقق من العقود من داخل البوت")
    else:
        try:
            acct = broker.account()
            print(f"\n✓ أبيان: المحفظة {acct.get('portfolio_id')} القوة الشرائية {acct.get('buying_power')}$")
            exp = broker.expirations(args.symbol)
            print(f"✓ أبيان: {len(exp)} تاريخ انتهاء للرمز {args.symbol}")
        except Exception as e:
            print(f"\n✗ أبيان: {e}")
    try:
        q = quotes_mod.build(env)
        print(f"\nمصدر الأسعار: {q.name}")
    except Exception as e:
        print(f"\n✗ مصدر الأسعار: {e}")
    now = dt.datetime.now(dt.timezone.utc)
    print(f"\nالآن: {cal.fmt_both(now)} — السوق {'مفتوح' if cal.is_open(now) else 'مغلق'}")


def cmd_probe(args) -> None:
    from . import probe
    env = Env.load()
    print(probe.run(env, args.minutes, args.which))


def cmd_login(args) -> None:
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession
    env = Env.load()
    if not (env.tg_api_id and env.tg_api_hash):
        sys.exit("عيّن TG_API_ID وTG_API_HASH أولًا (من my.telegram.org)")
    with TelegramClient(StringSession(), env.tg_api_id, env.tg_api_hash) as client:
        print("\nانسخ القيمة التالية إلى متغير البيئة TG_SESSION ولا تشاركها مع أحد:\n")
        print(client.session.save())


def cmd_replay(args) -> None:
    """Parse each block of a text file (blocks separated by a line '---')."""
    from .parser import parse
    env, settings = load()
    text = open(args.file, encoding="utf-8").read()
    for i, block in enumerate(b.strip() for b in text.split("\n---\n")):
        if not block:
            continue
        p = parse(block, settings.date_order)
        print(f"#{i + 1} [{p.kind}] {p.occ_symbol or '—'} دخول={p.entry_low}-{p.entry_high} "
              f"أهداف={p.targets} وقف={p.stop} معرف={p.alert_ref} جزئي={p.exit_fraction}")
        for e in p.errors:
            print(f"    ✗ {e}")


def cmd_report(args) -> None:
    env, settings = load()
    engine = make_engine(env, settings)
    print(reports.daily(engine) if args.kind == "daily" else reports.weekly(engine))


def main(argv=None) -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)   # its URLs contain the bot token
    ap = argparse.ArgumentParser(prog="alert_trader")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run")
    c = sub.add_parser("check")
    c.add_argument("--symbol", default="AAPL")
    p = sub.add_parser("probe")
    p.add_argument("--minutes", type=float, default=15)
    p.add_argument("--which", default="both", choices=("both", "botapi", "userbot"))
    sub.add_parser("login")
    r = sub.add_parser("replay")
    r.add_argument("file")
    rp = sub.add_parser("report")
    rp.add_argument("kind", choices=("daily", "weekly"))
    args = ap.parse_args(argv)
    try:
        {"run": cmd_run, None: cmd_run, "check": cmd_check, "probe": cmd_probe, "login": cmd_login,
         "replay": cmd_replay, "report": cmd_report}[args.cmd](args)
    except LiveTradingBlocked as e:
        sys.exit(f"التشغيل الحقيقي مرفوض: {e}")


if __name__ == "__main__":
    main()
