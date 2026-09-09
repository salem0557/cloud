"""Salem: "انت ما اعطيتني تقريرك ليوم الامس الورقي و الحقيقي كلهم ارسلو".

Two things had to be true for him to be right, and both were: the scheduler
never called the reports (tests/test_scheduler.py), and once a day is missed
there was no way to render it afterwards — daily_message() only ever knew
about `today`. A report that can only describe the current day cannot answer
"send me yesterday's".
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import mine
import paper


def test_paper_can_render_a_past_session():
    book = {"open": [], "last_daily": None, "closed": [
        {"ticker": "MU", "closed_at": "2026-09-08T16:00:00", "why": "take",
         "multiple": 1.4, "near_miss": False},
        {"ticker": "F", "closed_at": "2026-09-09T16:00:00", "why": "stop",
         "multiple": 0.6, "near_miss": False},
    ]}
    msg = paper.daily_message(book, day="2026-09-08")
    assert "2026-09-08" in msg
    assert "اليوم: 1 صفقات" in msg, "yesterday's session, not both days"
    assert "وصلت الهدف: 1" in msg


def test_paper_says_so_when_a_past_day_was_empty():
    book = {"open": [], "last_daily": None, "closed": []}
    assert "لا صفقات اليوم." in paper.daily_message(book, day="2026-09-08")


def test_mine_can_render_a_past_session():
    book = {"open": [], "closed": [
        {"ticker": "TSLA", "strike": 400, "exit_at": "2026-09-08T15:00:00",
         "multiple": 1.5, "entry_price": 2.0},
        {"ticker": "NVDA", "strike": 180, "exit_at": "2026-09-09T15:00:00",
         "multiple": 0.5, "entry_price": 2.0},
    ]}
    msg = mine.daily_message(book, day="2026-09-08")
    assert "2026-09-08" in msg
    assert "TSLA" in msg and "NVDA" not in msg


def test_today_is_still_the_default():
    import datetime
    today = datetime.date.today().isoformat()
    assert today in paper.daily_message({"open": [], "closed": []})
    assert today in mine.daily_message({"open": [], "closed": []})
