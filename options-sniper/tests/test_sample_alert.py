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
    """--plain, always. Railway runs with USE_CLAUDE_COMPOSER=0, so the
    deterministic renderer is the path a real alert takes there. The first
    version of this test omitted the flag, exercised the composer instead, and
    passed while render_entry crashed on Salem's container — a test that runs a
    different code path than production is not a test of production."""
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sample_alert.main(["--dry-run", "--plain"])
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


def test_the_reasoning_chain_reaches_the_message_railway_sends():
    """The chain was wired into the payload and into the Claude composer's
    instructions, but never into render_entry — the renderer that actually
    runs, since USE_CLAUDE_COMPOSER is 0 on the container. Every alert Salem
    would have received was missing the part that says why."""
    text = rendered()
    assert "كسر المقاومة 182.4" in text
    assert "الفكرة انتهت، اخرج" in text
    assert "يزيد العقد 44 سنت" in text


def test_an_empty_tier_is_shown_as_empty_not_hidden():
    """A budget with no qualifying contract has to say so — hiding it would
    imply three choices always exist."""
    assert "ما فيه عقد مناسب" in rendered()


def test_the_message_reads_in_the_order_a_decision_is_made():
    """Salem said the first version was hard to follow. It led with a score,
    then flow jargon, then a target, then contracts, then a separate exit
    table — five blocks before the first instruction. Why, then what to buy,
    then when to get out."""
    text = rendered()
    assert text.index("كسر المقاومة") < text.index("اشترِ الآن")
    assert text.index("اشترِ الآن") < text.index("بِع عند")


def test_nothing_the_reader_cannot_act_on_survives():
    """Everything removed was real and true and did not help him decide."""
    text = rendered()
    for jargon in ("ATR", "علاوة", "عند الطلب", "سويب", "خارج المال",
                   "دلتا العقد", "خطة الخروج", "على العقد لا السهم"):
        assert jargon not in text, jargon


def test_the_payload_has_the_shape_the_scanner_produces():
    """If the sample drifted from the live payload it would stop being a
    preview of anything."""
    p = sample_alert.payload()
    for key in ("ticker", "score", "score_breakdown", "direction", "spot",
                "flow_reason", "technical", "news", "tiers", "reasoning",
                "time_riyadh", "risk"):
        assert key in p, key
    assert p["reasoning"]["links"], "the chain must be built, not stubbed"


def test_every_tier_field_matches_what_the_renderer_reads():
    """The shape check above only looked at top-level keys, so it passed while
    `exit` was a tuple where compose() indexes it by name — and the sample
    crashed on send. A tier is checked field by field now, and `exit` is built
    by the scanner's own exit_rule() rather than written out."""
    from scoring import exit_rule
    for t in sample_alert.payload()["tiers"]:
        if not t.get("option_symbol"):
            continue
        for key in ("tier", "strike", "type", "expiry", "dte", "ask", "bid",
                    "cost", "delta", "open_interest", "expected_profit_pct",
                    "exit"):
            assert key in t, key
        assert t["exit"] == exit_rule(t["dte"])
        for key in ("take_pct", "stop_pct", "note", "dte"):
            assert key in t["exit"], key


def test_the_exit_plan_reaches_the_message():
    """It is the part that says when to get out. One line, not a table with a
    legend — the tiers share a rule almost always."""
    text = rendered()
    assert "بِع عند +60%  |  اقطع عند -40%" in text
