"""Shared tool-call budget: at most LIMIT tool calls in any rolling window, across all sessions.

Each call frees its slot WINDOW_SECONDS after it was made. Single process only, like the
session store; keep Cloud Run at one instance.
"""

import math
import os
import threading
import time
from collections import deque

LIMIT = int(os.environ.get("TOOL_CALLS_PER_MINUTE", "5"))
WINDOW_SECONDS = 60.0

_lock = threading.Lock()
_calls: deque[float] = deque()


def _prune(now: float) -> None:
    while _calls and now - _calls[0] >= WINDOW_SECONDS:
        _calls.popleft()


def _seconds_until_free(now: float) -> int:
    return max(1, math.ceil(WINDOW_SECONDS - (now - _calls[0]))) if _calls else 0


def acquire() -> tuple[bool, int]:
    """Take one slot. Returns (granted, seconds until the next slot frees)."""
    with _lock:
        now = time.monotonic()
        _prune(now)
        if len(_calls) >= LIMIT:
            return False, _seconds_until_free(now)
        _calls.append(now)
        return True, _seconds_until_free(now)


def status() -> dict:
    """Remaining calls now, and when each used slot frees (soonest first)."""
    with _lock:
        now = time.monotonic()
        _prune(now)
        frees_in = [max(1, math.ceil(WINDOW_SECONDS - (now - t))) for t in _calls]
        return {"limit": LIMIT, "remaining": max(0, LIMIT - len(_calls)),
                "window_seconds": int(WINDOW_SECONDS), "frees_in_seconds": frees_in}


def reset(limit: int | None = None) -> None:
    """Clear used slots (tests), optionally changing the limit."""
    global LIMIT
    with _lock:
        _calls.clear()
        if limit is not None:
            LIMIT = limit
