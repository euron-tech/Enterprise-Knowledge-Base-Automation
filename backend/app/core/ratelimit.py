"""Redis token bucket. Falls back to an in-process bucket when Redis is absent (dev/test)."""

from __future__ import annotations

import time
from collections import defaultdict

from app.core.config import get_settings
from app.core.errors import RateLimitError


class _MemoryBuckets:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)

    def hit(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        now = time.time()
        bucket = [t for t in self._hits[key] if now - t < window]
        bucket.append(now)
        self._hits[key] = bucket
        if len(bucket) > limit:
            retry = int(window - (now - bucket[0])) + 1
            return False, max(retry, 1)
        return True, 0

    def clear(self) -> None:
        self._hits.clear()


_memory = _MemoryBuckets()


class RateLimiter:
    def __init__(self, redis_client: object | None = None) -> None:
        self.redis = redis_client

    def policy_for(self, route: str) -> tuple[int, int]:
        limits = get_settings().rate_limits
        return limits.get(route, limits["default"])

    async def check(self, route: str, identity: str) -> None:
        limit, window = self.policy_for(route)
        key = f"rl:{route}:{identity}"
        if self.redis is not None:
            allowed, retry = await self._check_redis(key, limit, window)
        else:
            allowed, retry = _memory.hit(key, limit, window)
        if not allowed:
            raise RateLimitError(
                f"rate limit {limit}/{window}s exceeded for {route}",
                public_message=f"Rate limit exceeded. Retry in {retry}s.",
            )

    async def _check_redis(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        pipe = self.redis.pipeline()  # type: ignore[union-attr]
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = await pipe.execute()
        if ttl is None or ttl < 0:
            await self.redis.expire(key, window)  # type: ignore[union-attr]
            ttl = window
        if count > limit:
            return False, max(int(ttl), 1)
        return True, 0


def reset_memory_buckets() -> None:
    _memory.clear()
