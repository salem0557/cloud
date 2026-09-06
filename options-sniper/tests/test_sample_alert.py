"""The sample alert must be unmistakable as a sample.

Salem asked for a test message "about a trade you found". Nothing was found —
the market was closed and no scan had run. Sending invented figures dressed as
a live signal is exactly the failure of the system he ran before this one, so
the marking is not decoration and is tested like anything else.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import sample_alert


def rendered():
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sample_alert.main(["--dry-run"])
    return buf.getvalue()


def test_it_says_it_is_a_sample_before_any_number_appears():
    text = rendered()
    banner_at = text.index("رسالة تجريبية")
    assert banner_at < text.index("NVDA")
    assert "وليست صفقة حقيقية" in text


def test_it_says_so_again_at_the_end():
    """A long message on a phone is scrolled; the top marker can be off screen
    by the time the contracts are read."""
    assert "انتهت الرسالة التجريبية" in rendered().rstrip()[-80:]


def test_it_carries_the_real_reasoning_chain():
    """The point of the sample is to show the true format. If the chain were
    faked or omitted, it would be showing something that will never arrive."""
    text = rendered()
    assert "كسر المقاومة 182.4" in text
    assert "دلتا العقد 0.44" in text
    assert "تقدير بالدلتا وليس وعداً" in text


def test_an_empty_tier_is_shown_as_empty_not_hidden():
    """A budget with no qualifying contract has to say so — hiding it would
    imply three choices always exist."""
    assert "لا يوجد عقد مناسب" in rendered()


def test_the_payload_has_the_shape_the_scanner_produces():
    """If the sample drifted from the live payload it would stop being a
    preview of anything."""
    p = sample_alert.payload()
    for key in ("ticker", "score", "score_breakdown", "direction", "spot",
                "flow_reason", "technical", "news", "tiers", "reasoning",
                "time_riyadh", "risk"):
        assert key in p, key
    assert p["reasoning"]["links"], "the chain must be built, not stubbed"
