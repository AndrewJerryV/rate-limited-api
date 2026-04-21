from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status

from app.limiter_factory import create_rate_limiter
from app.models import RequestAcceptedResponse, RequestBody, StatsResponse
from app.rate_limiter import RateLimiter, RedisRateLimiter
from app.settings import settings

app = FastAPI(
    title="Rate-Limited API Service",
    description="Concurrency-safe rate limiter assignment with optional Redis backend.",
    version="1.0.0",
)

rate_limiter = create_rate_limiter(settings)


def get_rate_limiter() -> RateLimiter | RedisRateLimiter:
    return rate_limiter


@app.get("/")
def index() -> dict[str, object]:
    return {
        "service": "Rate-Limited API Service",
        "status": "running",
        "rate_limit": {
            "max_requests": rate_limiter.max_requests,
            "window_seconds": rate_limiter.window_seconds,
            "scope": "per user_id",
            "backend": rate_limiter.backend,
        },
        "endpoints": {
            "submit_request": "POST /request",
            "stats": "GET /stats",
            "health": "GET /health",
            "docs": "GET /docs",
        },
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/request",
    response_model=RequestAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_request(
    body: RequestBody,
    response: Response,
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RequestAcceptedResponse:
    decision = limiter.allow(body.user_id)

    response.headers["X-RateLimit-Limit"] = str(decision.limit)
    response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
    response.headers["X-RateLimit-Window-Seconds"] = str(decision.window_seconds)

    if not decision.allowed:
        response.headers["Retry-After"] = str(decision.retry_after_seconds)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "rate_limit_exceeded",
                "message": "Rate limit exceeded. Try again later.",
                "limit": decision.limit,
                "window_seconds": decision.window_seconds,
                "retry_after_seconds": decision.retry_after_seconds,
            },
            headers={
                "Retry-After": str(decision.retry_after_seconds),
                "X-RateLimit-Limit": str(decision.limit),
                "X-RateLimit-Remaining": str(decision.remaining),
                "X-RateLimit-Window-Seconds": str(decision.window_seconds),
            },
        )

    return RequestAcceptedResponse(
        status="accepted",
        user_id=body.user_id,
        remaining_requests=decision.remaining,
        limit=decision.limit,
        window_seconds=decision.window_seconds,
        idempotency_key=idempotency_key,
        payload=body.payload,
    )


@app.get("/stats", response_model=StatsResponse)
def get_stats(
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> StatsResponse:
    return StatsResponse(users=limiter.snapshot_stats())
