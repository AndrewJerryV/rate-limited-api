from dataclasses import dataclass
from os import getenv


@dataclass(frozen=True)
class Settings:
    rate_limit_backend: str = getenv("RATE_LIMIT_BACKEND", "redis").lower()
    redis_url: str = getenv("REDIS_URL", "redis://localhost:6379/0")
    max_requests: int = int(getenv("RATE_LIMIT_MAX_REQUESTS", "5"))
    window_seconds: int = int(getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))


settings = Settings()
