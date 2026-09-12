from analyst_agent import chart, indicators, verdict
from analyst_agent.tests.conftest import make_df


def _render(df, frame="1h"):
    facts = indicators.analyze(df, frame)
    facts["frame_label"] = "ساعة"
    call = verdict.decide(facts).to_dict()
    return chart.render("TSLA", df, "1 hour", facts, call)


def test_render_returns_a_png():
    png = _render(make_df(trend=0.5, seed=81))
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 20_000


def test_render_works_on_a_daily_frame_without_vwap():
    png = _render(make_df(trend=-0.4, seed=82, n=260, freq="1D", tz=None), "1d")
    assert png and png[:4] == b"\x89PNG"


def test_too_few_bars_returns_none():
    assert chart.render("X", make_df(n=10, seed=83), "1 hour", {"levels": {}}, None) is None


def test_missing_facts_do_not_raise():
    df = make_df(trend=0.2, seed=84)
    assert chart.render("X", df, "1 hour", {}, None)[:4] == b"\x89PNG"


def test_render_survives_a_broken_dataframe():
    df = make_df(seed=85).drop(columns=["Volume"])
    assert chart.render("X", df, "1 hour", {"levels": {}}, None) is None
