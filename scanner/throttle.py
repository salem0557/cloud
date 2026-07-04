"""Adaptive, self-recovering throttle for Yahoo rate limiting.

Temporary by design: when batches start coming back mostly empty (Yahoo
rejecting requests), an inter-batch delay kicks in and doubles on repeat
offenses; as soon as batches return healthy again the delay halves away
back to zero and the scanner runs at full speed.
"""
import logging

from . import config

log = logging.getLogger(__name__)

ESCALATE_BELOW = 0.5   # batch data ratio that signals rejection
RECOVER_ABOVE = 0.8    # batch data ratio considered healthy


class Throttle:
    def __init__(self, max_delay: float = None):
        self.delay = 0.0
        self.max_delay = max_delay or config.THROTTLE_MAX_DELAY

    def report(self, data_ratio: float):
        """Feed the outcome of one batch; adjusts the current delay."""
        if data_ratio < ESCALATE_BELOW:
            previous = self.delay
            self.delay = min(max(30.0, self.delay * 2), self.max_delay)
            if self.delay != previous:
                log.warning("Throttle escalated to %.0fs (batch ratio %.2f)",
                            self.delay, data_ratio)
        elif data_ratio > RECOVER_ABOVE and self.delay:
            self.delay = 0.0 if self.delay <= 8 else self.delay / 2
            if not self.delay:
                log.info("Throttle fully recovered")

    @property
    def active(self) -> bool:
        return self.delay > 0
