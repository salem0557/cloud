"""Salem, 2026-09-09: "ايه عادي ارتفاع 20 او 30 دولار لكن مايكون وصل قمته".

A rise before the alert is fine. Buying the top is not. These tests pin the
arithmetic that makes that sentence true, so a later loosening of the gate
has to argue with a failing test rather than with a comment.
"""
import config as C
import technical


def _tech(remaining, **kw):
    t = {"broke_level": True, "remaining_atr": remaining, "atr": 1.0}
    t.update(kw)
    return t


def test_at_most_a_third_of_the_move_may_be_gone():
    # target sits TARGET_ATR_MULT past the level; requiring MIN_REMAINING_ATR
    # of room means (1 - room/target) of the move may already be behind price.
    gone = 1 - C.MIN_REMAINING_ATR / C.TARGET_ATR_MULT
    assert gone <= 1 / 3 + 1e-9, (
        f"the rule allows {gone:.0%} of the move to be gone before it alerts; "
        "Salem asked for a rise, not the top")


def test_late_break_is_late():
    assert technical.is_late(_tech(C.MIN_REMAINING_ATR - 0.01))
    assert not technical.is_late(_tech(C.MIN_REMAINING_ATR))


def test_a_reversal_is_subject_to_the_room_gate_too():
    # A reversal never sets broke_level. It still carries a target, so it can
    # still be bought at the top, and the gate has to see it.
    rev = {"reversal": True, "remaining_atr": 0.1, "atr": 1.0,
           "broke_level": False}
    assert technical.has_setup(rev)
    assert technical.is_late(rev)


def test_flat_dict_is_not_a_setup():
    assert not technical.has_setup(None)
    assert not technical.has_setup({"broke_level": False})


def test_the_measurement_is_recorded_so_it_is_not_re_litigated():
    assert any("MIN_REMAINING_ATR" in k for k in C.SETTLED)
