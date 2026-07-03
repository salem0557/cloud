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
    # Alerts are streamed batch-by-batch during a scan: fresh_matches picks
    # what to send now, record remembers it, and prune (at scan end) forgets
    # symbols that stopped matching so they re-alert if the signal returns.

    def fresh_matches(self, matches) -> list:
        """Matches that are new or whose matched-filter set changed."""
        return [m for m in matches
                if self.last_alerts.get(m.symbol) != m.signature()]

    def record(self, matches):
        for m in matches:
            self.last_alerts[m.symbol] = m.signature()

    def prune(self, still_matching: set[str]):
        self.last_alerts = {s: sig for s, sig in self.last_alerts.items()
                            if s in still_matching}
