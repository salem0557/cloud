"""Headlines are context, not signal — so only real, recent ones are shown."""
from datetime import datetime, timedelta, timezone

import pytest

from analyst_agent import config, news


def _item(title, publisher, hours_old=2.0):
    when = datetime.now(timezone.utc) - timedelta(hours=hours_old)
    return {"content": {"title": title, "provider": {"displayName": publisher},
                        "pubDate": when.isoformat()}}


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(config, "NEWS_ENABLED", True)
    monkeypatch.setattr(config, "NEWS_MAX_AGE_HOURS", 48)


def _feed(monkeypatch, items):
    class FakeTicker:
        def __init__(self, symbol):
            self.news = items

    monkeypatch.setattr(news.yf, "Ticker", FakeTicker)


def test_promotional_publishers_are_dropped(monkeypatch):
    _feed(monkeypatch, [_item("Should you buy Costco?", "Motley Fool"),
                        _item("Costco misses on revenue", "Reuters")])
    titles = [h["title"] for h in news.headlines("COST")]
    assert titles == ["Costco misses on revenue"]


def test_stale_headlines_are_dropped(monkeypatch):
    _feed(monkeypatch, [_item("Old news", "Reuters", hours_old=200),
                        _item("Fresh news", "CNBC", hours_old=1)])
    assert [h["title"] for h in news.headlines("COST")] == ["Fresh news"]


def test_reporting_outranks_the_rest(monkeypatch):
    _feed(monkeypatch, [_item("Blog take", "Some Blog", hours_old=1),
                        _item("Wire story", "Reuters", hours_old=5)])
    assert news.headlines("COST")[0]["publisher"] == "Reuters"


def test_freshest_first_within_the_same_rank(monkeypatch):
    _feed(monkeypatch, [_item("Older", "Reuters", hours_old=10),
                        _item("Newer", "Bloomberg", hours_old=1)])
    assert news.headlines("COST")[0]["title"] == "Newer"


def test_each_headline_carries_its_age(monkeypatch):
    _feed(monkeypatch, [_item("Story", "Reuters", hours_old=3)])
    item = news.headlines("COST")[0]
    assert item["age_hours"] == pytest.approx(3.0, abs=0.2)
    assert "قبل 3 ساعة" == item["age_label"]


@pytest.mark.parametrize("hours,expected", [
    (0.2, "قبل دقائق"), (5, "قبل 5 ساعة"), (50, "قبل 2 يوم"), (None, ""),
])
def test_age_label(hours, expected):
    assert news.age_label(hours) == expected


def test_age_from_an_epoch_timestamp():
    epoch = (datetime.now(timezone.utc) - timedelta(hours=4)).timestamp()
    assert news.age_hours(epoch) == pytest.approx(4.0, abs=0.2)


def test_unparseable_dates_do_not_drop_the_headline(monkeypatch):
    _feed(monkeypatch, [{"content": {"title": "No date", "provider":
                                     {"displayName": "Reuters"}, "pubDate": "soon"}}])
    assert [h["title"] for h in news.headlines("COST")] == ["No date"]


def test_a_broken_feed_is_not_fatal(monkeypatch):
    class Boom:
        def __init__(self, symbol):
            raise RuntimeError("network")

    monkeypatch.setattr(news.yf, "Ticker", Boom)
    assert news.headlines("COST") == []


def test_news_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(config, "NEWS_ENABLED", False)
    assert news.headlines("COST") == []
