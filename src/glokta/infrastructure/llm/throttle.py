"""A minimal request-pacing rate limiter for CTI inference.

Enforces a minimum interval between calls (``60 / rpm`` seconds). The clock and sleep are
injectable so pacing is testable without real waits. Not thread-safe — one limiter per worker.
"""

import time
from typing import Callable


class RateLimiter:
    def __init__(
        self,
        rpm: int,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.min_interval = 60.0 / rpm if rpm > 0 else 0.0
        self._sleep = sleep
        self._monotonic = monotonic
        self._last: float | None = None

    def wait(self) -> None:
        """Block until at least ``min_interval`` has passed since the previous call."""
        if self.min_interval <= 0:
            return
        now = self._monotonic()
        if self._last is not None:
            elapsed = now - self._last
            if elapsed < self.min_interval:
                self._sleep(self.min_interval - elapsed)
        self._last = self._monotonic()
