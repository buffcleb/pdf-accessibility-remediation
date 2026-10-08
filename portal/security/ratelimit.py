"""Fixed-window rate limits in Redis, shared by every API instance."""
from __future__ import annotations

from functools import lru_cache

import redis

from portal.config import get_portal_settings


@lru_cache
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(get_portal_settings().redis_url)


class RateLimiter:
    def __init__(self, client: redis.Redis):
        self.r = client

    def hit(self, bucket: str, key: str, limit: int, window_seconds: int) -> bool:
        """Count one attempt; return False when the limit for this window is exceeded."""
        name = f"rl:{bucket}:{key}"
        pipe = self.r.pipeline()
        pipe.incr(name)
        pipe.expire(name, window_seconds, nx=True)
        count, _ = pipe.execute()
        return int(count) <= limit


def get_rate_limiter() -> RateLimiter:
    return RateLimiter(get_redis())
