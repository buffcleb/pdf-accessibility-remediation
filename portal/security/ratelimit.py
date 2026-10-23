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


# Per-person limits on what's costly to serve (#357): files downloaded, reports exported, upload requests. Each is a
# setting (per hour); past it the request gets a plain 429 saying when to try again.
PER_HOUR = {"download": "rate_downloads_per_hour", "export": "rate_exports_per_hour", "upload": "rate_uploads_per_hour",
            "document_export": "rate_document_exports_per_hour"}


def per_person(bucket: str, scope: str | None = None):
    """A FastAPI dependency: count one `bucket` action for the signed-in person, refuse it past the hourly limit.
    `scope`: on a document route a script's API token may use (#518), the permission it takes; the token's owner is
    counted, under the same limits."""
    from fastapi import Depends

    from portal.config import get_portal_settings
    from portal.deps import Principal, current_principal, document_principal
    from portal.errors import ApiError

    who = document_principal(scope) if scope else current_principal

    def check(p: Principal = Depends(who), limiter: RateLimiter = Depends(get_rate_limiter)) -> None:
        limit = getattr(get_portal_settings(), PER_HOUR[bucket])
        if limit > 0 and not limiter.hit(bucket, str(p.user.id), limit, 3600):
            raise ApiError(429, "rate_limited", f"That's a lot of {_WORDS[bucket]} in an hour. Wait a while and try "
                                                "again, or ask your administrator if you need more.")
    return check


_WORDS = {"download": "downloads", "export": "exports", "upload": "uploads", "document_export": "zip downloads"}
