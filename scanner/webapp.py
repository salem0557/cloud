"""لوحة ويب للقراءة فقط (read-only) فوق بيانات البوت المسجَّلة فعلياً --
كتالوج عقود، حالة السوق العام، أخبار، ومحلل آلي مبني على قواعد. تعمل
كخادم tornado صغير (tornado موجودة أصلاً كتبعية لـ
python-telegram-bot[webhooks]، فلا حاجة لتبعية جديدة) داخل نفس عملية
البوت -- انظر bot.py's post_init لآلية التشغيل.

مستقلة تماماً عن ويب-هوك تيليجرام: منفذ (port) مختلف عمداً
(DASHBOARD_PORT، افتراضياً 8090 مقابل WEBHOOK_PORT/PORT الافتراضي 8080)
حتى لا يتصادما حتى لو فُعِّلا معاً. البيانات المعروضة "حقيقية" بمعنى أنها
مسجَّلة فعلياً من جلسات /options سابقة (signals.db) -- وليست فحصاً حياً
يبدأ عند فتح الصفحة (فحص كامل يستغرق دقائق، أطول من مهلة أي طلب HTTP
معقول).
"""
import asyncio
import json
import logging
import os

import tornado.web
from tornado.httpserver import HTTPServer

from . import config, dashboard_data, market_module, signals_db

log = logging.getLogger(__name__)

_WEB_DIR = os.path.join(os.path.dirname(__file__), "web")

_server: HTTPServer | None = None


class _JSONHandler(tornado.web.RequestHandler):
    """Base class: every dashboard API response is JSON, and a handler
    failure must surface as a normal error payload instead of a raw 500
    stack trace leaking to the browser."""

    def write_json(self, payload, status: int = 200):
        self.set_status(status)
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.write(json.dumps(payload, ensure_ascii=False))

    def write_error(self, status_code, **kwargs):
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.write(json.dumps({"error": "internal_error", "status": status_code}, ensure_ascii=False))


class CatalogHandler(_JSONHandler):
    async def get(self):
        rows = await asyncio.to_thread(
            signals_db.fetch_catalog_signals, config.DASHBOARD_CATALOG_MAX_AGE_DAYS)
        contracts = dashboard_data.build_catalog(rows)
        self.write_json({
            "contracts": contracts,
            "count": len(contracts),
            "max_age_days": config.DASHBOARD_CATALOG_MAX_AGE_DAYS,
        })


class MarketHandler(_JSONHandler):
    async def get(self):
        self.write_json(await market_module.market_status())


class NewsHandler(_JSONHandler):
    async def get(self):
        symbols_param = self.get_query_argument("symbols", "")
        extra = [s.strip().upper() for s in symbols_param.split(",") if s.strip()][:10]
        items = await market_module.fetch_news(extra)
        self.write_json({"items": items, "count": len(items)})


class BriefingHandler(_JSONHandler):
    """يجمع حالة السوق + أبرز الأخبار + نصائح تداول عامة برد واحد -- صفحة
    "ملخص السوق اليومي" بلوحة الويب (حلّت محل صفحتَي حالة السوق والأخبار
    المنفصلتين)."""

    async def get(self):
        symbols_param = self.get_query_argument("symbols", "")
        extra = [s.strip().upper() for s in symbols_param.split(",") if s.strip()][:10]
        market = await market_module.market_status()
        news = await market_module.fetch_news(extra)
        tips = market_module.generate_trading_tips(market)
        self.write_json({"market": market, "news": news, "tips": tips})


class AnalystHandler(_JSONHandler):
    async def get(self):
        id_param = self.get_query_argument("id", None)
        if not id_param or not id_param.isdigit():
            self.write_json({"error": "missing_or_invalid_id"}, status=400)
            return
        row = await asyncio.to_thread(signals_db.fetch_signal_by_id, int(id_param))
        if row is None or row["section"] not in ("options", "leaps", "heavy"):
            self.write_json({"error": "not_found"}, status=404)
            return
        contract = dashboard_data.row_to_contract(row)
        market = await market_module.market_status()
        opinion = dashboard_data.generate_analyst_opinion(contract, market)
        self.write_json({"contract": contract, "opinion": opinion})


def _build_app() -> tornado.web.Application:
    return tornado.web.Application([
        (r"/api/catalog", CatalogHandler),
        (r"/api/market", MarketHandler),
        (r"/api/news", NewsHandler),
        (r"/api/briefing", BriefingHandler),
        (r"/api/analyst", AnalystHandler),
        (r"/(.*)", tornado.web.StaticFileHandler,
         {"path": _WEB_DIR, "default_filename": "index.html"}),
    ])


async def start_dashboard() -> bool:
    """يبدأ خادم لوحة الويب مرة واحدة فقط (استدعاء ثانٍ لا يفعل شيئاً --
    راجع bot.py's post_init، الذي قد يُستدعى مرتين في مسار فشل الويب-هوك
    ثم الرجوع لـ polling). يرجع True لو بدأ فعلياً، False لو كان معطّلاً
    (DASHBOARD_ENABLED=false) أو فشل البدء (يُسجَّل تحذير فقط -- فشل هنا
    لا يجب أن يوقف بوت تيليجرام نفسه إطلاقاً)."""
    global _server
    if _server is not None:
        return False
    if not config.DASHBOARD_ENABLED:
        log.info("Dashboard disabled (DASHBOARD_ENABLED=false)")
        return False
    try:
        app = _build_app()
        server = HTTPServer(app)
        server.listen(config.DASHBOARD_PORT, address=config.DASHBOARD_LISTEN)
        _server = server
        log.info("Dashboard listening on %s:%s", config.DASHBOARD_LISTEN, config.DASHBOARD_PORT)
        return True
    except Exception:
        log.exception("Dashboard failed to start on %s:%s -- continuing without it",
                      config.DASHBOARD_LISTEN, config.DASHBOARD_PORT)
        return False
