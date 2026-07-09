"""Persistent bot state: the fixed roster of already-approved members.

The bot no longer has an /approve command (see bot.py) -- membership is a
frozen snapshot of whoever was approved before this restructure. Editing it
now requires hand-editing the state file on disk; there is no in-bot way to
add a new member anymore.
"""
import json
import logging
import os
import tempfile

from . import config

log = logging.getLogger(__name__)


class State:
    def __init__(self, path: str = None):
        self.path = path or config.STATE_FILE
        self.approved: dict[str, float] = {}   # chat id -> sub expiry ts (0 = lifetime)
        self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                raw = json.load(f)
            self.approved = dict(raw.get("approved", {}))
        except (OSError, ValueError):
            pass

    def save(self):
        payload = {"approved": self.approved}
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
