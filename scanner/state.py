"""Persistent bot state: subscribed chats + last-sent alerts (for dedup)."""
import json
import logging
import os
import tempfile
import time

from . import config

log = logging.getLogger(__name__)


class State:
    def __init__(self, path: str = None):
        self.path = path or config.STATE_FILE
        self.subscribers: set[int] = set()
        self.accepted: dict[str, float] = {}   # chat id -> disclaimer accept time
        self.approved: dict[str, float] = {}   # chat id -> sub expiry ts (0 = lifetime)
        # symbol -> {"sig": alert signature, "ts": last time it matched}
        self.last_alerts: dict[str, dict] = {}
        self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                raw = json.load(f)
            self.subscribers = set(raw.get("subscribers", []))
            self.accepted = dict(raw.get("accepted", {}))
            self.approved = dict(raw.get("approved", {}))
            now = time.time()
            for sym, entry in dict(raw.get("last_alerts", {})).items():
                if isinstance(entry, str):  # legacy format: bare signature
                    entry = {"sig": entry, "ts": now}
                self.last_alerts[sym] = entry
        except (OSError, ValueError):
            pass

    def save(self):
        payload = {
            "subscribers": sorted(self.subscribers),
            "accepted": self.accepted,
            "approved": self.approved,
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
    # what to send now, record refreshes the memory of everything currently
    # matching, and prune (at scan end) only forgets a symbol after its
    # signal has been gone for ALERT_MEMORY_HOURS. The time buffer matters:
    # a transient batch-download failure or a filter flickering off for one
    # scan must NOT cause the identical alert to be sent again next hour.

    def fresh_matches(self, matches) -> list:
        """Matches that are new or whose matched-filter set changed."""
        fresh = []
        for m in matches:
            entry = self.last_alerts.get(m.symbol)
            if entry is None or entry["sig"] != m.signature():
                fresh.append(m)
        return fresh

    def record(self, matches):
        now = time.time()
        for m in matches:
            self.last_alerts[m.symbol] = {"sig": m.signature(), "ts": now}

    def prune(self):
        cutoff = time.time() - config.ALERT_MEMORY_HOURS * 3600
        self.last_alerts = {s: e for s, e in self.last_alerts.items()
                            if e["ts"] >= cutoff}
