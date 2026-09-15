"""ANALYST_OWNER_IDS accepts both spellings in one variable."""
import importlib


def _reload(monkeypatch, value):
    monkeypatch.setenv("ANALYST_OWNER_IDS", value)
    from analyst_agent import config

    return importlib.reload(config)


def test_numeric_owners(monkeypatch):
    config = _reload(monkeypatch, "123, 456")
    assert config.OWNER_IDS == {123, 456}
    assert config.OWNER_USERNAMES == set()


def test_named_owners(monkeypatch):
    config = _reload(monkeypatch, "@salem0557")
    assert config.OWNER_IDS == set()
    assert config.OWNER_USERNAMES == {"salem0557"}


def test_both_spellings_together(monkeypatch):
    config = _reload(monkeypatch, "@salem0557, 123456 @other")
    assert config.OWNER_IDS == {123456}
    assert config.OWNER_USERNAMES == {"salem0557", "other"}


def test_an_empty_setting(monkeypatch):
    config = _reload(monkeypatch, "")
    assert config.OWNER_IDS == set() and config.OWNER_USERNAMES == set()
