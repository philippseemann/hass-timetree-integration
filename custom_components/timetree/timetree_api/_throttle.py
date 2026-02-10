"""Request throttle to enforce minimum delay between API calls."""

from __future__ import annotations

import asyncio
import time

from .const import DEFAULT_THROTTLE_SECONDS


class RequestThrottle:
    """Ensures a minimum interval between consecutive API requests.

    The TimeTree web app queues requests with ~100ms spacing.
    This class replicates that behavior to avoid triggering rate limits.
    """

    def __init__(self, min_interval: float = DEFAULT_THROTTLE_SECONDS) -> None:
        self._min_interval = min_interval
        self._last_request: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until the minimum interval has elapsed since the last request."""
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()
