import os

os.environ["RATE_LIMIT_BACKEND"] = "memory"

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app, get_rate_limiter
from app.rate_limiter import RateLimiter


class ManualClock:
    def __init__(self, now: float = 0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def limiter(clock: ManualClock) -> RateLimiter:
    return RateLimiter(max_requests=5, window_seconds=60, clock=clock)


@pytest.fixture(autouse=True)
def override_limiter(limiter: RateLimiter):
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


@pytest.mark.anyio
async def test_accepts_five_requests_per_user_per_minute(client: AsyncClient):
    print("\nChecking one user can make exactly 5 accepted requests per minute.")
    for _ in range(5):
        response = await client.post(
            "/request",
            json={"user_id": "alice", "payload": {"action": "ping"}},
        )
        assert response.status_code == 202
    print("First 5 requests returned HTTP 202 Accepted.")

    response = await client.post(
        "/request",
        json={"user_id": "alice", "payload": {"action": "ping"}},
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert response.json()["detail"]["error"] == "rate_limit_exceeded"
    print("Sixth request returned HTTP 429 with Retry-After header.")


@pytest.mark.anyio
async def test_limit_is_per_user(client: AsyncClient):
    print("\nChecking rate limit is isolated per user_id.")
    for _ in range(5):
        assert (
            await client.post("/request", json={"user_id": "alice", "payload": {}})
        ).status_code == 202
    print("Alice used all 5 allowed requests.")

    bob_response = await client.post(
        "/request",
        json={"user_id": "bob", "payload": {"independent": True}},
    )

    assert bob_response.status_code == 202
    print("Bob still receives HTTP 202 because limits are per user.")


@pytest.mark.anyio
async def test_window_resets_after_sixty_seconds(
    client: AsyncClient,
    clock: ManualClock,
):
    print("\nChecking the rolling window allows requests after 60 seconds.")
    for _ in range(5):
        await client.post("/request", json={"user_id": "alice", "payload": {}})

    assert (
        await client.post("/request", json={"user_id": "alice", "payload": {}})
    ).status_code == 429
    print("Alice is rate limited before the window expires.")

    clock.advance(60)
    print("Manual test clock advanced by 60 seconds.")

    response = await client.post(
        "/request",
        json={"user_id": "alice", "payload": {"after": "reset"}},
    )

    assert response.status_code == 202
    assert response.json()["remaining_requests"] == 4
    print("New request is accepted after old timestamps expire.")


@pytest.mark.anyio
async def test_parallel_requests_remain_rate_limited(client: AsyncClient):
    print("\nChecking concurrent requests cannot bypass the limit.")
    responses = await asyncio_gather_requests(client, total=20, user_id="parallel-user")

    accepted = [response for response in responses if response.status_code == 202]
    rejected = [response for response in responses if response.status_code == 429]

    assert len(accepted) == 5
    assert len(rejected) == 15
    print("20 parallel requests produced exactly 5 accepted and 15 rejected.")


@pytest.mark.anyio
async def test_stats_include_user_request_counts(client: AsyncClient):
    print("\nChecking /stats reports accepted, rejected, and active window counts.")
    for _ in range(5):
        await client.post("/request", json={"user_id": "alice", "payload": {}})
    await client.post("/request", json={"user_id": "alice", "payload": {}})

    response = await client.get("/stats")

    assert response.status_code == 200
    alice = response.json()["users"]["alice"]
    assert alice["total_requests"] == 6
    assert alice["accepted_requests"] == 5
    assert alice["rejected_requests"] == 1
    assert alice["active_window_requests"] == 5
    assert alice["remaining_requests"] == 0
    print("/stats returned total=6, accepted=5, rejected=1, remaining=0.")


async def asyncio_gather_requests(
    client: AsyncClient,
    total: int,
    user_id: str,
):
    import asyncio

    return await asyncio.gather(
        *[
            client.post(
                "/request",
                json={"user_id": user_id, "payload": {"request_number": index}},
            )
            for index in range(total)
        ]
    )
