"""
Egyszerű in-memory TTL cache.
"""
import time
from threading import Lock

_cache = {}
_lock = Lock()


def get(key):
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.time() > expires_at:
            del _cache[key]
            return None
        return value


def set(key, value, ttl_seconds=300):
    with _lock:
        _cache[key] = (value, time.time() + ttl_seconds)


def delete(key):
    with _lock:
        _cache.pop(key, None)


def clear():
    with _lock:
        _cache.clear()
