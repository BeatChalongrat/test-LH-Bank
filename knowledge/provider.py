"""Respect upstream rate limits independently for each API endpoint and worker."""

import threading
import time
from email.utils import parsedate_to_datetime


class ProviderCoolingDown(Exception):
    pass


class ProviderBackoff:
    def __init__(self):
        self.until = 0.0
        self.lock = threading.Lock()

    def check(self):
        if time.monotonic() < self.until:
            raise ProviderCoolingDown()

    def observe(self, response):
        if response.status_code != 429:
            return
        value = response.headers.get("Retry-After", "1")
        try:
            delay = float(value)
        except ValueError:
            try:
                delay = parsedate_to_datetime(value).timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                delay = 1
        if not 0 <= delay < float("inf"):
            delay = 1
        with self.lock:
            self.until = max(self.until, time.monotonic() + max(1, delay))
