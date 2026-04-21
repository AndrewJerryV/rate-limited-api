from app.rate_limiter import RateLimiter, RedisRateLimiter
from app.settings import Settings


def create_rate_limiter(settings: Settings) -> RateLimiter | RedisRateLimiter:
    if settings.rate_limit_backend == "redis":
        return RedisRateLimiter(
            redis_url=settings.redis_url,
            max_requests=settings.max_requests,
            window_seconds=settings.window_seconds,
        )

    if settings.rate_limit_backend != "memory":
        raise ValueError(
            "RATE_LIMIT_BACKEND must be either 'memory' or 'redis'."
        )

    return RateLimiter(
        max_requests=settings.max_requests,
        window_seconds=settings.window_seconds,
    )
