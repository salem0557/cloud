"""The pinned header must be read from config, never typed.

A message pinned at the top of the channel saying "30 alerts a day" while
MAX_ALERTS_PER_DAY is 5 is worse than no message: it is a promise the system
does not keep, sitting there for a month.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config as C
import intro


def test_the_alert_header_quotes_the_configured_cadence(monkeypatch):
    monkeypatch.setattr(C, "SCAN_EVERY_MIN", 10)
    monkeypatch.setattr(C, "MONITOR_EVERY_MIN", 5)
    monkeypatch.setattr(C, "MAX_ALERTS_PER_DAY", 30)
    t = intro.alerts_text()
    assert "كل 10 دقائق" in t
    assert "كل 5 دقائق" in t
    assert "30 في اليوم" in t


def test_the_alert_header_follows_the_exit_rule(monkeypatch):
    monkeypatch.setattr(C, "EXIT_RULES", [(0, 55, -25, "")])
    t = intro.alerts_text()
    assert "+55%" in t and "-25%" in t


def test_the_paper_header_quotes_the_measured_baseline(monkeypatch):
    monkeypatch.setattr(C, "PAPER_BASELINE",
                        {"hit": 11.1, "lost": 22.2, "avg": 1.234})
    t = intro.paper_text()
    assert "11.1%" in t and "22.2%" in t and "$1.234" in t


def test_the_paper_header_says_plainly_that_nothing_is_bought():
    """Salem asked whether this section buys and sells. It does not, and the
    header has to say so before anything else."""
    t = intro.paper_text()
    assert "لا شراء ولا بيع حقيقي" in t


def test_each_header_goes_to_its_own_section(monkeypatch):
    sent = []
    monkeypatch.setattr(intro, "send", lambda t: sent.append(("alerts", t)) or True)
    monkeypatch.setattr(intro, "send_paper",
                        lambda t: sent.append(("paper", t)) or True)
    intro.main([])
    assert [w for w, _ in sent] == ["alerts", "paper"]
    assert "تنبيهات الدخول" in sent[0][1]
    assert "التداول الورقي" in sent[1][1]


def test_only_sends_the_section_asked_for(monkeypatch):
    sent = []
    monkeypatch.setattr(intro, "send", lambda t: sent.append("alerts") or True)
    monkeypatch.setattr(intro, "send_paper", lambda t: sent.append("paper") or True)
    intro.main(["--only", "paper"])
    assert sent == ["paper"]


def test_a_dry_run_sends_nothing(monkeypatch, capsys):
    def boom(_):
        raise AssertionError("--dry-run must not send")
    monkeypatch.setattr(intro, "send", boom)
    monkeypatch.setattr(intro, "send_paper", boom)
    intro.main(["--dry-run"])
    assert "هذا القسم" in capsys.readouterr().out
