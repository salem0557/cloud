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


VISION_HINTS = ("llama-4", "vision", "vl", "scout", "maverick")


def vision_capable(models: list[str]) -> list[str]:
    """Served ids that look like they can read an image."""
    return [m for m in models if any(hint in m.lower() for hint in VISION_HINTS)]


def model_candidates(kind: str = "text") -> list[str]:
    """Every model worth trying for this job, best first.

    Groq retires model ids without notice, and a key may not carry every
    model, so the caller walks this list instead of betting on one id.
    """
    explicit = config.GROQ_VISION_MODEL if kind == "vision" else config.GROQ_TEXT_MODEL
    preference = (config.VISION_MODEL_PREFERENCE if kind == "vision"
                  else config.TEXT_MODEL_PREFERENCE)
    live = available_models()
    ordered: list[str] = []

    def add(name: str | None) -> None:
        if name and name not in ordered:
            ordered.append(name)

    add(explicit)                                   # an explicit choice wins
    for candidate in preference:                    # preferred and confirmed live
        if candidate in live:
            add(candidate)
    for candidate in (vision_capable(live) if kind == "vision" else live):
        add(candidate)                              # anything else Groq serves
    if not live:                                    # /models unreachable: guess
        for candidate in preference:
            add(candidate)
    return ordered


def resolve_model(kind: str = "text") -> str:
    """The first model worth calling for this job."""
    candidates = model_candidates(kind)
    preference = (config.VISION_MODEL_PREFERENCE if kind == "vision"
                  else config.TEXT_MODEL_PREFERENCE)
    return candidates[0] if candidates else preference[0]


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
    candidates = [model] if model else model_candidates(kind)
    if not candidates:
        candidates = [resolve_model(kind)]
    model = candidates[0]
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
        elif resp.status_code in (400, 404) and "model" in resp.text.lower():
            # This id is retired, or this key does not carry it: try the next.
            last_error = f"model {model} unavailable"
            log.warning("model %s rejected (%s)", model, resp.status_code)
            candidates = [c for c in candidates if c != model]
            if not candidates:
                available_models(force=True)         # maybe the list is stale
                candidates = [c for c in model_candidates(kind) if c != model]
            if candidates:
                model = candidates[0]
                body["model"] = model
                log.warning("switching to %s", model)
                continue
            served = ", ".join(available_models()[:8]) or "تعذّر جلب القائمة"
            raise GroqError(f"لا يوجد موديل صالح لـ {kind}. المتاح لمفتاحك: {served}")
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
