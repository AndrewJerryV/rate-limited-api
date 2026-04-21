from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from threading import RLock
from time import monotonic, time
from uuid import uuid4
from typing import Callable

from app.models import UserStatsResponse


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    window_seconds: int
    retry_after_seconds: int


@dataclass
class UserStats:
    total_requests: int = 0
    accepted_requests: int = 0
    rejected_requests: int = 0
    last_request_at: str | None = None


class RateLimiter:
    """Concurrency-safe in-memory sliding-window rate limiter.

    The lock protects the complete check-and-update operation. That makes the
    limiter accurate when parallel requests for the same user arrive at once.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: int,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if max_requests < 1:
            raise ValueError("max_requests must be at least 1")
        if window_seconds < 1:
            raise ValueError("window_seconds must be at least 1")

        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.backend = "memory"
        self._clock = clock
        self._lock = RLock()
        self._request_windows: dict[str, deque[float]] = defaultdict(deque)
        self._stats: dict[str, UserStats] = defaultdict(UserStats)

    def allow(self, user_id: str) -> RateLimitDecision:
        now = self._clock()
        requested_at = self._utc_now()

        with self._lock:
            window = self._request_windows[user_id]
            self._prune(window, now)

            stats = self._stats[user_id]
            stats.total_requests += 1
            stats.last_request_at = requested_at

            if len(window) >= self.max_requests:
                retry_after = max(1, ceil(self.window_seconds - (now - window[0])))
                stats.rejected_requests += 1
                return RateLimitDecision(
                    allowed=False,
                    limit=self.max_requests,
                    remaining=0,
                    window_seconds=self.window_seconds,
                    retry_after_seconds=retry_after,
                )

            window.append(now)
            stats.accepted_requests += 1
            remaining = self.max_requests - len(window)

            return RateLimitDecision(
                allowed=True,
                limit=self.max_requests,
                remaining=remaining,
                window_seconds=self.window_seconds,
                retry_after_seconds=0,
            )

    def snapshot_stats(self) -> dict[str, UserStatsResponse]:
        now = self._clock()

        with self._lock:
            response: dict[str, UserStatsResponse] = {}

            for user_id, stats in self._stats.items():
                window = self._request_windows[user_id]
                self._prune(window, now)
                active_window_requests = len(window)
                response[user_id] = UserStatsResponse(
                    total_requests=stats.total_requests,
                    accepted_requests=stats.accepted_requests,
                    rejected_requests=stats.rejected_requests,
                    active_window_requests=active_window_requests,
                    remaining_requests=max(0, self.max_requests - active_window_requests),
                    limit=self.max_requests,
                    window_seconds=self.window_seconds,
                    last_request_at=stats.last_request_at,
                )

            return response

    def _prune(self, window: deque[float], now: float) -> None:
        oldest_allowed = now - self.window_seconds
        while window and window[0] <= oldest_allowed:
            window.popleft()

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class RedisRateLimiter:
    """Redis-backed sliding-window limiter.

    Redis executes Lua scripts atomically, so the check-and-update operation is
    safe across parallel requests, multiple app processes, and multiple servers.
    """

    _ALLOW_SCRIPT = """
    local requests_key = KEYS[1]
    local stats_key = KEYS[2]
    local users_key = KEYS[3]

    local limit = tonumber(ARGV[1])
    local window_ms = tonumber(ARGV[2])
    local user_id = ARGV[3]
    local request_id = ARGV[4]
    local requested_at = ARGV[5]

    local redis_time = redis.call("TIME")
    local now_ms = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
    local window_start = now_ms - window_ms
    local window_seconds = math.ceil(window_ms / 1000)
    local stats_ttl = 7 * 24 * 60 * 60

    redis.call("ZREMRANGEBYSCORE", requests_key, 0, window_start)
    local current_count = redis.call("ZCARD", requests_key)

    redis.call("SADD", users_key, user_id)
    redis.call("HINCRBY", stats_key, "total_requests", 1)
    redis.call("HSET", stats_key, "last_request_at", requested_at)

    if current_count >= limit then
        redis.call("HINCRBY", stats_key, "rejected_requests", 1)
        redis.call("EXPIRE", requests_key, window_seconds * 2)
        redis.call("EXPIRE", stats_key, stats_ttl)

        local oldest = redis.call("ZRANGE", requests_key, 0, 0, "WITHSCORES")
        local retry_after_seconds = 1
        if oldest[2] ~= nil then
            retry_after_seconds = math.max(
                1,
                math.ceil((window_ms - (now_ms - tonumber(oldest[2]))) / 1000)
            )
        end

        return {0, limit, 0, window_seconds, retry_after_seconds}
    end

    redis.call("ZADD", requests_key, now_ms, request_id)
    redis.call("HINCRBY", stats_key, "accepted_requests", 1)
    redis.call("EXPIRE", requests_key, window_seconds * 2)
    redis.call("EXPIRE", stats_key, stats_ttl)

    current_count = current_count + 1
    return {1, limit, limit - current_count, window_seconds, 0}
    """

    def __init__(
        self,
        redis_url: str,
        max_requests: int,
        window_seconds: int,
        key_prefix: str = "source_asia_rate_limiter",
    ) -> None:
        if max_requests < 1:
            raise ValueError("max_requests must be at least 1")
        if window_seconds < 1:
            raise ValueError("window_seconds must be at least 1")

        try:
            from redis import Redis
        except ImportError as exc:
            raise RuntimeError(
                "Redis backend selected but the redis package is not installed."
            ) from exc

        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.backend = "redis"
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._redis.ping()
        self._key_prefix = key_prefix
        self._allow_script = self._redis.register_script(self._ALLOW_SCRIPT)

    def allow(self, user_id: str) -> RateLimitDecision:
        result = self._allow_script(
            keys=[
                self._requests_key(user_id),
                self._stats_key(user_id),
                self._users_key(),
            ],
            args=[
                self.max_requests,
                self.window_seconds * 1000,
                user_id,
                f"{int(time() * 1000)}:{uuid4()}",
                self._utc_now(),
            ],
        )

        return RateLimitDecision(
            allowed=bool(int(result[0])),
            limit=int(result[1]),
            remaining=int(result[2]),
            window_seconds=int(result[3]),
            retry_after_seconds=int(result[4]),
        )

    def snapshot_stats(self) -> dict[str, UserStatsResponse]:
        now_ms = int(time() * 1000)
        window_start = now_ms - (self.window_seconds * 1000)
        response: dict[str, UserStatsResponse] = {}

        for user_id in self._redis.smembers(self._users_key()):
            requests_key = self._requests_key(user_id)
            stats_key = self._stats_key(user_id)
            self._redis.zremrangebyscore(requests_key, 0, window_start)

            active_window_requests = int(self._redis.zcard(requests_key))
            stats = self._redis.hgetall(stats_key)
            response[user_id] = UserStatsResponse(
                total_requests=int(stats.get("total_requests", 0)),
                accepted_requests=int(stats.get("accepted_requests", 0)),
                rejected_requests=int(stats.get("rejected_requests", 0)),
                active_window_requests=active_window_requests,
                remaining_requests=max(0, self.max_requests - active_window_requests),
                limit=self.max_requests,
                window_seconds=self.window_seconds,
                last_request_at=stats.get("last_request_at"),
            )

        return response

    def _requests_key(self, user_id: str) -> str:
        return f"{self._key_prefix}:requests:{user_id}"

    def _stats_key(self, user_id: str) -> str:
        return f"{self._key_prefix}:stats:{user_id}"

    def _users_key(self) -> str:
        return f"{self._key_prefix}:users"

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
