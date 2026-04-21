from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(..., min_length=1, max_length=128)
    payload: Any


class RequestAcceptedResponse(BaseModel):
    status: str
    user_id: str
    remaining_requests: int
    limit: int
    window_seconds: int
    idempotency_key: str | None = None
    payload: Any


class UserStatsResponse(BaseModel):
    total_requests: int
    accepted_requests: int
    rejected_requests: int
    active_window_requests: int
    remaining_requests: int
    limit: int
    window_seconds: int
    last_request_at: str | None


class StatsResponse(BaseModel):
    users: dict[str, UserStatsResponse]
