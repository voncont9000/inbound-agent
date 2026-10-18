from __future__ import annotations

import time
from collections.abc import Mapping
from threading import Lock
from typing import Protocol

from loguru import logger
from redis import asyncio as redis_asyncio


class ReplayGuard(Protocol):
    async def claim(self, key: str, ttl_seconds: int) -> bool: ...


class InMemoryReplayGuard:
    def __init__(self) -> None:
        self._expires_at: dict[str, float] = {}
        self._lock = Lock()

    async def claim(self, key: str, ttl_seconds: int) -> bool:
        with self._lock:
            now = time.time()
            for existing_key, expires_at in list(self._expires_at.items()):
                if expires_at < now:
                    del self._expires_at[existing_key]
            existing_expiry = self._expires_at.get(key)
            if existing_expiry is not None and existing_expiry >= now:
                return False
            self._expires_at[key] = now + ttl_seconds
            return True


class RedisReplayGuard:
    def __init__(self, client: redis_asyncio.Redis, prefix: str = "inbound:replay:") -> None:
        self.client = client
        self.prefix = prefix
        self._fallback = InMemoryReplayGuard()

    async def claim(self, key: str, ttl_seconds: int) -> bool:
        try:
            result = await self.client.set(
                self.prefix + key,
                "1",
                nx=True,
                ex=ttl_seconds,
            )
        except Exception as error:
            logger.error(
                "Redis replay protection failed; using in-memory guard: {}",
                error,
            )
            return await self._fallback.claim(key, ttl_seconds)
        return bool(result)


def replay_guard_from_env(env: Mapping[str, str]) -> ReplayGuard:
    redis_url = env.get("REDIS_URL", "").strip()
    if not redis_url:
        return InMemoryReplayGuard()
    return RedisReplayGuard(redis_asyncio.from_url(redis_url))
