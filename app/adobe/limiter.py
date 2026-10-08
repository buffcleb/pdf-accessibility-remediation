"""Rate limiting shared by every worker that talks to Adobe.

Two limits, both from settings because Adobe doesn't publish them:
  * requests per minute (ADOBE_RPM), counted in a fixed one-minute window;
  * documents at Adobe at once (ADOBE_MAX_CONCURRENT), as leases that expire if a worker dies.
When Adobe answers 429 with Retry-After, `pause()` holds back every worker until then.

State lives in Redis so the limits hold across worker processes and machines. With an in-memory
broker (tests, single-process runs) it falls back to process-local counters.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import AsyncIterator, Protocol

from app.config import get_settings

_PREFIX = "adobe:limit"

# Atomic lease: drop expired leases, then take one if there's room.
_LEASE = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', tonumber(ARGV[1]) - tonumber(ARGV[2]))
if redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[3]) then
  redis.call('ZADD', KEYS[1], ARGV[1], ARGV[4])
  return 1
end
return 0
"""


class Limiter(Protocol):
    async def request(self) -> None: ...
    def slot(self) -> contextlib.AbstractAsyncContextManager[None]: ...
    async def pause(self, seconds: float) -> None: ...


class _Base:
    def __init__(self, rpm: int, max_concurrent: int, lease_seconds: float, clock=time.time, sleep=asyncio.sleep):
        self.rpm = max(1, rpm)
        self.max_concurrent = max(1, max_concurrent)
        self.lease_seconds = lease_seconds
        self.clock, self.sleep = clock, sleep

    async def request(self) -> None:
        """Wait until one more request fits in this minute (and any pause has passed)."""
        while True:
            wait = await self._paused_for()
            if wait > 0:
                await self.sleep(wait)
                continue
            window = int(self.clock() // 60)
            if await self._count(window) <= self.rpm:
                return
            await self.sleep(max(0.05, (window + 1) * 60 - self.clock()))

    @contextlib.asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Hold one of the concurrent document slots for the duration of the block."""
        lease = uuid.uuid4().hex
        while not await self._take(lease):
            await self.sleep(1.0)
        try:
            yield
        finally:
            await self._give(lease)

    async def _paused_for(self) -> float: ...
    async def _count(self, window: int) -> int: ...
    async def _take(self, lease: str) -> bool: ...
    async def _give(self, lease: str) -> None: ...


class MemoryLimiter(_Base):
    """Process-local limits (tests, single-process runs)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._counts: dict[int, int] = {}
        self._leases: dict[str, float] = {}
        self._pause_until = 0.0

    async def pause(self, seconds: float) -> None:
        self._pause_until = max(self._pause_until, self.clock() + seconds)

    async def _paused_for(self) -> float:
        return self._pause_until - self.clock()

    async def _count(self, window: int) -> int:
        for w in [w for w in self._counts if w < window]:
            del self._counts[w]
        self._counts[window] = self._counts.get(window, 0) + 1
        return self._counts[window]

    async def _take(self, lease: str) -> bool:
        now = self.clock()
        for k in [k for k, t in self._leases.items() if t < now - self.lease_seconds]:
            del self._leases[k]
        if len(self._leases) >= self.max_concurrent:
            return False
        self._leases[lease] = now
        return True

    async def _give(self, lease: str) -> None:
        self._leases.pop(lease, None)


class RedisLimiter(_Base):
    def __init__(self, url: str, *a, **kw):
        super().__init__(*a, **kw)
        import redis.asyncio as redis

        self.r = redis.from_url(url)
        self._lease = self.r.register_script(_LEASE)

    async def pause(self, seconds: float) -> None:
        until = self.clock() + seconds
        current = await self.r.get(f"{_PREFIX}:pause")
        if current is None or float(current) < until:
            await self.r.set(f"{_PREFIX}:pause", until, ex=int(seconds) + 5)

    async def _paused_for(self) -> float:
        v = await self.r.get(f"{_PREFIX}:pause")
        return float(v) - self.clock() if v is not None else 0.0

    async def _count(self, window: int) -> int:
        key = f"{_PREFIX}:rpm:{window}"
        async with self.r.pipeline(transaction=True) as p:
            p.incr(key)
            p.expire(key, 120)
            n, _ = await p.execute()
        return int(n)

    async def _take(self, lease: str) -> bool:
        return bool(await self._lease(keys=[f"{_PREFIX}:inflight"],
                                      args=[self.clock(), self.lease_seconds, self.max_concurrent, lease]))

    async def _give(self, lease: str) -> None:
        await self.r.zrem(f"{_PREFIX}:inflight", lease)


def get_limiter() -> Limiter:
    s = get_settings()
    # A lease outlives the longest a chunk may take, so a crashed worker's slot frees itself.
    args = (s.adobe_rpm, s.adobe_max_concurrent, s.adobe_timeout_minutes * 60 + 120)
    if s.redis_url.startswith("memory://"):
        return MemoryLimiter(*args)
    return RedisLimiter(s.redis_url, *args)
