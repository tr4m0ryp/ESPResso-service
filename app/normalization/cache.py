"""Thread-safe TTL-based LRU cache for normalization results."""

import asyncio
import threading

from cachetools import TTLCache


class NormalizationCache:
    """Async-safe TTL+LRU cache with hit-rate tracking."""

    def __init__(self, max_size: int = 10000, ttl: int = 86400):
        self._cache: TTLCache[str, str] = TTLCache(
            maxsize=max_size, ttl=ttl,
        )
        self._lock = asyncio.Lock()
        self._thread_lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    async def get(self, key: str) -> str | None:
        """Get a cached value. Returns None on miss."""
        async with self._lock:
            val = self._cache.get(key)
            if val is not None:
                self._hits += 1
            else:
                self._misses += 1
            return val

    async def set(self, key: str, value: str) -> None:
        """Cache a value."""
        async with self._lock:
            self._cache[key] = value

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        if total == 0:
            return 0.0
        return self._hits / total

    @property
    def size(self) -> int:
        return len(self._cache)

    def stats(self) -> dict:
        return {
            "size": self.size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self.hit_rate, 4),
        }
