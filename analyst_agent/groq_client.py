"""Groq API client — the only outbound AI call in the agent.

Two jobs: read a chart screenshot with a vision model, and write the Arabic
analysis with a text model. Model ids are resolved against Groq's live
/models list, so a deprecated model degrades to the next preference instead of
turning every request into a 404.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import time

import requests

from . import config

log = logging.getLogger(__name__)

THINK_TAGS = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
MAX_IMAGE_BYTES = 3_500_000   # Groq rejects inline images above ~4MB base64
MAX_IMAGE_SIDE = 1400


class GroqError(RuntimeError):
    """Any failure that leaves us without a usable completion."""


_model_cache: dict[str, tuple[float, list[str]]] = {}


def available_models(force: bool = False) -> list[str]:
    """Model ids Groq currently serves for this key ([] if the call fails)."""
    cached = _model_cache.get("models")
    if cached and not force and time.time() - cached[0] < config.MODEL_CACHE_TTL:
        return cached[1]
    if not config.GROQ_API_KEY:
        return []
    try:
        resp = requests.get(
            f"{config.GROQ_BASE_URL}/models",
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            timeout=15,
        )
        resp.raise_for_status()
        ids = [m.get("id") for m in resp.json().get("data", []) if m.get("id")]
    except Exception:
        log.warning("Groq /models failed; falling back to configured ids", exc_info=True)
        ids = []
    _model_cache["models"] = (time.time(), ids)
    return ids


def resolve_model(kind: str = "text") -> str:
    """Pick the model to call: explicit env value, else best live preference."""
    explicit = config.GROQ_VISION_MODEL if kind == "vision" else config.GROQ_TEXT_MODEL
    if explicit:
        return explicit
    preference = (config.VISION_MODEL_PREFERENCE if kind == "vision"
                  else config.TEXT_MODEL_PREFERENCE)
    live = available_models()
    if live:
        for candidate in preference:
            if candidate in live:
                return candidate
        # Nothing preferred is live: take any served model that looks capable.
        for candidate in live:
            if kind == "vision" and ("llama-4" in candidate or "vision" in candidate):
                return candidate
        if kind == "text" and live:
            return live[0]
    return preference[0]


def prepare_image(data: bytes) -> str:
    """Chart screenshot -> data URL small enough for the vision endpoint."""
    mime = "image/png"
    payload = data
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        if max(img.size) > MAX_IMAGE_SIDE:
            ratio = MAX_IMAGE_SIDE / max(img.size)
            img = img.resize((max(1, int(img.width * ratio)),
                              max(1, int(img.height * ratio))), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88, optimize=True)
        payload, mime = buf.getvalue(), "image/jpeg"
        quality = 88
        while len(payload) > MAX_IMAGE_BYTES and quality > 40:
            quality -= 15
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            payload = buf.getvalue()
    except Exception:
        log.warning("Pillow unavailable or image unreadable; sending original bytes")
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _clean(text: str) -> str:
    return THINK_TAGS.sub("", text or "").strip()


def chat(messages: list[dict], kind: str = "text", json_mode: bool = False,
         temperature: float | None = None, max_tokens: int | None = None,
         model: str | None = None) -> str:
    """One completion. Retries 429/5xx with backoff, then raises GroqError."""
    if not config.GROQ_API_KEY:
        raise GroqError("GROQ_API_KEY غير مضبوط")
    model = model or resolve_model(kind)
    body = {
        "model": model,
        "messages": messages,
        "temperature": config.GROQ_TEMPERATURE if temperature is None else temperature,
        "max_tokens": max_tokens or config.GROQ_MAX_TOKENS,
        "stream": False,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    last_error = "unknown"
    for attempt in range(config.GROQ_MAX_RETRIES):
        try:
            resp = requests.post(
                f"{config.GROQ_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {config.GROQ_API_KEY}",
                         "Content-Type": "application/json"},
                json=body, timeout=config.GROQ_TIMEOUT,
            )
        except Exception as exc:  # network-level
            last_error = f"network: {exc}"
            time.sleep(2 ** attempt)
            continue

        if resp.status_code == 200:
            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = _clean(message.get("content") or "")
            if not content and message.get("reasoning"):
                content = _clean(message["reasoning"])
            if content:
                return content
            last_error = f"empty completion (finish={choice.get('finish_reason')})"
        elif resp.status_code in (429, 500, 502, 503, 520, 529):
            retry_after = resp.headers.get("retry-after")
            delay = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() \
                else 2 ** attempt
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            log.warning("Groq %s, retrying in %.1fs", resp.status_code, delay)
            time.sleep(min(delay, 20))
            continue
        elif resp.status_code == 404 and not (config.GROQ_TEXT_MODEL or config.GROQ_VISION_MODEL):
            # The model id went away: refresh the live list and try the next one.
            available_models(force=True)
            new_model = resolve_model(kind)
            last_error = f"model {model} unavailable"
            if new_model != model:
                log.warning("model %s unavailable, switching to %s", model, new_model)
                body["model"] = model = new_model
                continue
        else:
            raise GroqError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        time.sleep(1)
    raise GroqError(last_error)


def vision_chat(prompt: str, image: bytes, system: str | None = None,
                json_mode: bool = True, temperature: float = 0.0) -> str:
    """Ask the vision model about one image."""
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": prepare_image(image)}},
    ]
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})
    return chat(messages, kind="vision", json_mode=json_mode,
                temperature=temperature, max_tokens=1200)


def parse_json(text: str) -> dict:
    """Best-effort JSON out of a model reply (fenced, prefixed or bare)."""
    if not text:
        return {}
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}
