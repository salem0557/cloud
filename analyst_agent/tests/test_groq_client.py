import json

import pytest

from analyst_agent import config, groq_client


class FakeResponse:
    def __init__(self, status=200, payload=None, text="", headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def _completion(text):
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}


@pytest.fixture(autouse=True)
def _key_and_clean_cache(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(config, "GROQ_TEXT_MODEL", "")
    monkeypatch.setattr(config, "GROQ_VISION_MODEL", "")
    monkeypatch.setattr(groq_client, "_model_cache", {})
    monkeypatch.setattr(groq_client.time, "sleep", lambda *_: None)


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 2}\n```', {"a": 2}),
    ('sure, here it is: {"a": 3} hope that helps', {"a": 3}),
    ("no json at all", {}),
    ("", {}),
    ("[1,2,3]", {}),
])
def test_parse_json(text, expected):
    assert groq_client.parse_json(text) == expected


def test_think_tags_are_stripped():
    assert groq_client._clean("<think>reasoning</think>\nالجواب") == "الجواب"


def test_resolve_model_prefers_a_live_preference(monkeypatch):
    live = ["llama-3.3-70b-versatile", "whisper-large-v3"]
    monkeypatch.setattr(groq_client, "available_models", lambda force=False: live)
    monkeypatch.setattr(config, "TEXT_MODEL_PREFERENCE",
                        ["does-not-exist", "llama-3.3-70b-versatile"])
    assert groq_client.resolve_model("text") == "llama-3.3-70b-versatile"


def test_explicit_model_env_wins(monkeypatch):
    monkeypatch.setattr(config, "GROQ_TEXT_MODEL", "my/model")
    monkeypatch.setattr(groq_client, "available_models", lambda force=False: [])
    assert groq_client.resolve_model("text") == "my/model"


def test_resolve_model_falls_back_to_first_preference_when_offline(monkeypatch):
    monkeypatch.setattr(groq_client, "available_models", lambda force=False: [])
    monkeypatch.setattr(config, "VISION_MODEL_PREFERENCE", ["vision/one", "vision/two"])
    assert groq_client.resolve_model("vision") == "vision/one"


def test_chat_returns_content(monkeypatch):
    monkeypatch.setattr(groq_client.requests, "post",
                        lambda *a, **k: FakeResponse(200, _completion("تحليل")))
    assert groq_client.chat([{"role": "user", "content": "x"}], model="m") == "تحليل"


def test_chat_retries_rate_limit_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def post(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse(429, {}, "slow down", {"retry-after": "0"})
        return FakeResponse(200, _completion("ok"))

    monkeypatch.setattr(groq_client.requests, "post", post)
    assert groq_client.chat([{"role": "user", "content": "x"}], model="m") == "ok"
    assert calls["n"] == 2


def test_chat_switches_model_on_404(monkeypatch):
    """A model id that Groq has retired must not kill the request."""
    seen = []
    live = {"ids": ["dead/model"]}

    def post(url, **kwargs):
        seen.append(kwargs["json"]["model"])
        if len(seen) == 1:
            return FakeResponse(404, {}, "model_not_found")
        return FakeResponse(200, _completion("done"))

    def fake_available(force=False):
        if force:                       # the retry refreshes the live list
            live["ids"] = ["live/model"]
        return live["ids"]

    monkeypatch.setattr(groq_client.requests, "post", post)
    monkeypatch.setattr(config, "TEXT_MODEL_PREFERENCE", ["dead/model", "live/model"])
    monkeypatch.setattr(groq_client, "available_models", fake_available)
    assert groq_client.chat([{"role": "user", "content": "x"}]) == "done"
    assert seen == ["dead/model", "live/model"]


def test_chat_raises_on_bad_key(monkeypatch):
    monkeypatch.setattr(groq_client.requests, "post",
                        lambda *a, **k: FakeResponse(401, {}, "invalid api key"))
    with pytest.raises(groq_client.GroqError):
        groq_client.chat([{"role": "user", "content": "x"}], model="m")


def test_chat_without_key_raises(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    with pytest.raises(groq_client.GroqError):
        groq_client.chat([{"role": "user", "content": "x"}])


def test_prepare_image_returns_a_small_data_url():
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (3000, 2000), "navy").save(buf, format="PNG")
    url = groq_client.prepare_image(buf.getvalue())
    assert url.startswith("data:image/jpeg;base64,")
    assert len(url) < groq_client.MAX_IMAGE_BYTES * 1.4


def test_vision_chat_sends_image_and_text(monkeypatch):
    captured = {}

    def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return '{"is_chart": true}'

    monkeypatch.setattr(groq_client, "chat", fake_chat)
    groq_client.vision_chat("read it", b"\x89PNG\r\n\x1a\n")
    parts = captured["messages"][-1]["content"]
    assert parts[0]["type"] == "text"
    assert parts[1]["image_url"]["url"].startswith("data:image/")
    assert captured["kwargs"]["kind"] == "vision"


def test_vision_capable_filters_the_served_list():
    served = ["meta-llama/llama-4-scout-17b-16e-instruct", "llama-3.3-70b-versatile",
              "whisper-large-v3", "some/vision-model"]
    capable = groq_client.vision_capable(served)
    assert "meta-llama/llama-4-scout-17b-16e-instruct" in capable
    assert "some/vision-model" in capable
    assert "whisper-large-v3" not in capable


def test_candidates_skip_a_retired_preference(monkeypatch):
    monkeypatch.setattr(groq_client, "available_models",
                        lambda force=False: ["meta-llama/llama-4-scout-17b-16e-instruct"])
    monkeypatch.setattr(config, "VISION_MODEL_PREFERENCE",
                        ["gone/model", "meta-llama/llama-4-scout-17b-16e-instruct"])
    candidates = groq_client.model_candidates("vision")
    assert candidates[0] == "meta-llama/llama-4-scout-17b-16e-instruct"
    assert "gone/model" not in candidates


def test_candidates_fall_back_to_preference_when_models_is_unreachable(monkeypatch):
    monkeypatch.setattr(groq_client, "available_models", lambda force=False: [])
    monkeypatch.setattr(config, "VISION_MODEL_PREFERENCE", ["a/model", "b/model"])
    assert groq_client.model_candidates("vision") == ["a/model", "b/model"]


def test_chat_walks_past_every_dead_model(monkeypatch):
    """A retired id must cost one retry, not the whole request."""
    seen = []

    def post(url, **kwargs):
        seen.append(kwargs["json"]["model"])
        if len(seen) < 3:
            return FakeResponse(404, {}, '{"error":{"message":"model not found"}}')
        return FakeResponse(200, _completion("تحليل"))

    monkeypatch.setattr(groq_client.requests, "post", post)
    monkeypatch.setattr(groq_client, "available_models",
                        lambda force=False: ["a/model", "b/model", "c/model"])
    monkeypatch.setattr(config, "TEXT_MODEL_PREFERENCE", ["a/model", "b/model", "c/model"])
    assert groq_client.chat([{"role": "user", "content": "x"}]) == "تحليل"
    assert seen == ["a/model", "b/model", "c/model"]


def test_chat_reports_what_the_key_actually_has(monkeypatch):
    monkeypatch.setattr(groq_client.requests, "post",
                        lambda *a, **k: FakeResponse(404, {}, '{"error":"model not found"}'))
    monkeypatch.setattr(groq_client, "available_models",
                        lambda force=False: ["llama-3.3-70b-versatile"])
    monkeypatch.setattr(config, "VISION_MODEL_PREFERENCE", ["dead/vision"])
    with pytest.raises(groq_client.GroqError) as excinfo:
        groq_client.chat([{"role": "user", "content": "x"}], kind="vision")
    assert "llama-3.3-70b-versatile" in str(excinfo.value)
