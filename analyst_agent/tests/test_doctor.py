"""The health check must always produce a full report — a broken piece shows
as a failed line, never as an exception that hides the other checks."""
import pytest

from analyst_agent import config, doctor


def test_packages_check_passes_here():
    check = doctor.check_packages()
    assert check.ok is True


def test_missing_groq_key_is_fatal(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    check = doctor.check_groq_key()
    assert check.ok is False and check.fatal is True
    assert "GROQ_API_KEY" in check.detail


def test_present_groq_key_is_reported_without_leaking_it(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk_supersecretvalue")
    check = doctor.check_groq_key()
    assert check.ok is True
    assert "gsk_supersecretvalue" not in check.detail


def test_telegram_check_reads_privacy_mode(monkeypatch):
    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "result": {"username": "analyst_bot",
                                           "can_read_all_group_messages": False}}

    monkeypatch.setenv("ANALYST_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(doctor.requests, "get", lambda *a, **k: Resp())
    check = doctor.check_telegram()
    assert check.ok is False                    # privacy mode still on
    assert "setprivacy" in check.detail


def test_telegram_check_passes_when_privacy_is_off(monkeypatch):
    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "result": {"username": "analyst_bot",
                                           "can_read_all_group_messages": True}}

    monkeypatch.setenv("ANALYST_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(doctor.requests, "get", lambda *a, **k: Resp())
    check = doctor.check_telegram()
    assert check.ok is True and "@analyst_bot" in check.detail


def test_rejected_token_is_fatal(monkeypatch):
    class Resp:
        status_code = 401

        @staticmethod
        def json():
            return {"ok": False, "description": "Unauthorized"}

    monkeypatch.setenv("ANALYST_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(doctor.requests, "get", lambda *a, **k: Resp())
    check = doctor.check_telegram()
    assert check.ok is False and check.fatal is True
    assert "Unauthorized" in check.detail


def test_userbot_session_counts_as_an_interface(monkeypatch):
    monkeypatch.delenv("ANALYST_BOT_TOKEN", raising=False)
    monkeypatch.setattr(config, "TELEGRAM_SESSION", "1BQANsession")
    assert doctor.check_telegram().ok is True


def test_no_interface_at_all_is_fatal(monkeypatch):
    monkeypatch.delenv("ANALYST_BOT_TOKEN", raising=False)
    monkeypatch.setattr(config, "TELEGRAM_SESSION", "")
    check = doctor.check_telegram()
    assert check.ok is False and check.fatal is True


def test_chart_check_renders():
    assert doctor.check_chart().ok is True


def test_session_and_config_checks_always_pass():
    assert doctor.check_session().ok is True
    assert doctor.check_config().ok is True


def test_a_check_that_explodes_is_reported_not_raised(monkeypatch):
    def boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(doctor, "QUICK", [boom])
    checks = doctor.run_all(quick=True)
    assert len(checks) == 1
    assert checks[0].ok is False and "kaboom" in checks[0].detail


def test_report_names_what_to_fix():
    checks = [doctor.Check("أ", True, "تمام"),
              doctor.Check("ب", False, "معطّل", fatal=True)]
    text = doctor.report(checks)
    assert "✅ أ" in text and "❌ ب" in text
    assert "لن يعمل" in text and "ب" in text


def test_report_says_all_clear():
    text = doctor.report([doctor.Check("أ", True, "تمام")])
    assert "كل شيء سليم" in text


def test_report_separates_warnings_from_failures():
    text = doctor.report([doctor.Check("أ", False, "ملاحظة", fatal=False)])
    assert "⚠️" in text and "يعمل مع ملاحظات" in text


def test_quick_mode_skips_the_slow_checks():
    names = {func.__name__ for func in doctor.QUICK}
    assert "check_pipeline" not in names
    assert "check_market_data" not in names
    assert "check_packages" in names


def test_main_exit_code_reflects_fatal_failures(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "QUICK", [lambda: doctor.Check("x", False, "boom", True)])
    assert doctor.main(["--quick"]) == 1
    assert "🩺" in capsys.readouterr().out
    monkeypatch.setattr(doctor, "QUICK", [lambda: doctor.Check("x", True, "fine")])
    assert doctor.main(["--quick"]) == 0
