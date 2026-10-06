"""Tiny in-process brute-force limiter for password prompts."""
from __future__ import annotations

import threading
import time

_WINDOW, _LIMIT = 300, 12        # 12 failures per 5 minutes per key
_fails: dict[str, list[float]] = {}
_lock = threading.Lock()


def blocked_for(key: str) -> int:
    """Seconds until ``key`` may try again (0 = allowed)."""
    now = time.time()
    with _lock:
        recent = [t for t in _fails.get(key, []) if now - t < _WINDOW]
        _fails[key] = recent
        if len(recent) >= _LIMIT:
            return int(_WINDOW - (now - recent[0])) + 1
    return 0


def record_failure(key: str) -> None:
    with _lock:
        _fails.setdefault(key, []).append(time.time())
        if len(_fails) > 5000:   # bound memory
            _fails.clear()


def reset(key: str) -> None:
    with _lock:
        _fails.pop(key, None)
