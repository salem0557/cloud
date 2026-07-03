"""Persistent bot state: subscribed chats + last-sent alerts (for dedup)."""
import json
import logging
import os
import tempfile

from . import config

log = logging.getLogger(__name__)


class State:
    def __init__(self, path: str = None):
        self.path = path or config.STATE_FILE
        self.subscribers: set[int] = set()
        self.last_alerts: dict[str, str] = {}  # symbol -> alert signature
        self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                raw = json.load(f)
            self.subscribers = set(raw.get("subscribers", []))
            self.last_alerts = dict(raw.get("last_alerts", {}))
        except (OSError, ValueError):
            pass

    def save(self):
        payload = {
            "subscribers": sorted(self.subscribers),
            "last_alerts": self.last_alerts,
        }
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path) or ".")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            log.exception("Failed to save state")
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # ------------------------------------------------------------- dedup
    def diff_alerts(self, matches) -> list:
        """Return only matches that are new or whose matched-filter set changed
        since the last scan, then remember the current scan as the baseline."""
        fresh = [m for m in matches
                 if self.last_alerts.get(m.symbol) != m.signature()]
        self.last_alerts = {m.symbol: m.signature() for m in matches}
        return fresh
