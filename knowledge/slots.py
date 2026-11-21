"""Prefer idle llama.cpp slots that already hold the requested evidence prefix."""

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class Slot:
    endpoint: str
    number: int
    replica: int
    prefix: str = ""
    pending: int = 0
    touched: float = 0


class Slots:
    def __init__(self, servers):
        self.lock = threading.Lock()
        self.slots = []
        for replica, (url, count) in enumerate(servers):
            if not 1 <= count <= 128:
                raise ValueError("Configure the actual llama.cpp slot count (1–128)")
            self.slots.extend(
                Slot(url.rstrip("/").removesuffix("/v1") + "/completion", n, replica)
                for n in range(count)
            )

    @contextmanager
    def lease(self, prefix):
        with self.lock:
            slot = min(
                self.slots,
                key=lambda s: (
                    s.pending,
                    s.prefix != prefix,
                    s.touched,
                ),
            )
            slot.prefix, slot.pending, slot.touched = (
                prefix,
                slot.pending + 1,
                time.monotonic(),
            )
        try:
            yield slot
        finally:
            with self.lock:
                slot.pending -= 1
