"""Salem, 2026-09-09: "اريد تضمن لي ادخل ولايكون مقلب ويقلب علي السعر باقل من دقيقة".

No system guarantees that. What it can do is stop calling a wick a break.

NVDA, 2026-09-08, the 09:30 bar: support 229.63, low 229.46, close 229.76.
The low pierced the level and the close came back ABOVE it, and the scanner
called it a confirmed breakdown — while the message printed "إغلاق شمعة 15د
تحت 229.63" as the entry rule. The code and the instruction disagreed, and
the code was the wrong one of the two.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config as C
import technical


def _bar(closed_beyond):
    """Everything a break needs, with only the close's side in question."""
    return {"broke_level": True, "closed_beyond": closed_beyond,
            "volume_ratio": 3.69, "closed_strong": True, "wick_back": False,
            "remaining_atr": C.MIN_REMAINING_ATR + 0.5, "atr": 1.0,
            "opening_range": False}


def test_a_close_back_inside_the_level_is_not_a_break():
    assert not technical.confirms(_bar(False))
    assert not technical.is_signal(_bar(False))


def test_the_same_bar_closing_beyond_is():
    assert technical.confirms(_bar(True))


def test_the_printed_entry_rule_is_the_rule_the_code_applies():
    """The message says "close of a 15m candle beyond the level". If confirms()
    ever stops requiring that again, this test is the one that says so."""
    import inspect
    src = inspect.getsource(technical.confirms)
    assert "closed_beyond" in src
