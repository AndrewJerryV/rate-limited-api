# Rate-Limited API Service

Production-style FastAPI service for the Source Asia assignment.

## Live Demo

- GitHub repository: `https://github.com/AndrewJerryV/rate-limited-api`
- Hugging Face Space: `https://huggingface.co/spaces/AndrewJerryV/rate-limited-api`
- API docs: `https://AndrewJerryV-rate-limited-api.hf.space/docs`
- Health check: `https://AndrewJerryV-rate-limited-api.hf.space/health`

It exposes:

- `POST /request` - accepts `{ "user_id": "...", "payload": ... }`
- `GET /stats` - returns per-user request statistics
- `GET /` - service summary
- `GET /health` - health check

The service enforces a per-user rate limit of **5 accepted requests per rolling 60-second window**.

## Bonus Features Included

- Redis-backed rate limiter using an atomic Lua script.
- In-memory fallback for tests or simple single-process runs.
- `Retry-After` response header for rate-limited clients.
- Retrying client script in `scripts/retry_client.py`.
- Dockerfile and Docker Compose setup.
- Hugging Face Spaces deployment.

## Tech Stack

- Python 3.11+
- FastAPI
- Uvicorn
- Redis default backend
- Pytest
- HTTPX

## Run Locally

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
docker run --rm -p 6379:6379 redis:7-alpine
```

In another terminal:

```bash
uvicorn app.main:app --reload
```

The API will be available at:

```text
http://127.0.0.1:8000
```

Swagger docs:

```text
http://127.0.0.1:8000/docs
```

## Run With Redis Locally

Redis is the default backend. It keeps rate limits accurate across multiple app processes and multiple deployed instances.

Fastest option:

```bash
docker compose up --build
```

Manual option:

```bash
docker run --rm -p 6379:6379 redis:7-alpine
```

In another terminal:

```bash
set REDIS_URL=redis://localhost:6379/0
uvicorn app.main:app --reload
```

PowerShell version:

```powershell
$env:REDIS_URL="redis://localhost:6379/0"
python -m uvicorn app.main:app --reload
```

## Example Requests

Accepted request:

```bash
curl -X POST http://127.0.0.1:8000/request ^
  -H "Content-Type: application/json" ^
  -d "{\"user_id\":\"user-123\",\"payload\":{\"message\":\"hello\"}}"
```

Rate limit exceeded response:

```json
{
  "detail": {
    "error": "rate_limit_exceeded",
    "message": "Rate limit exceeded. Try again later.",
    "limit": 5,
    "window_seconds": 60,
    "retry_after_seconds": 58
  }
}
```

Stats:

```bash
curl http://127.0.0.1:8000/stats
```

Example response:

```json
{
  "users": {
    "user-123": {
      "total_requests": 6,
      "accepted_requests": 5,
      "rejected_requests": 1,
      "active_window_requests": 5,
      "remaining_requests": 0,
      "limit": 5,
      "window_seconds": 60,
      "last_request_at": "2026-04-21T10:00:00Z"
    }
  }
}
```

## Run Tests

```bash
pytest
```

The test suite includes a parallel-request test that sends concurrent calls for the same user and verifies that exactly 5 are accepted while the rest receive HTTP `429`.

## Design Decisions

### Sliding Window Rate Limiter

The limiter stores timestamps of accepted requests per user and removes timestamps older than 60 seconds on each check. This is stricter and smoother than a fixed-minute bucket because users cannot burst at the boundary between two clock minutes.

### Concurrency Safety

All reads and writes to the in-memory request window and stats dictionaries happen inside a `threading.RLock`.

This matters because ASGI servers can process multiple requests concurrently. Without the lock, two parallel requests could both observe that a user has remaining capacity and both be accepted, causing the limiter to exceed 5 requests.

### Accurate Stats

The service tracks:

- total attempts
- accepted requests
- rejected requests
- currently active accepted requests in the rolling window
- remaining requests before the user is limited
- last request timestamp

### Clear Error Response

When a user exceeds the limit, the API returns HTTP `429 Too Many Requests` with a structured error body and a `Retry-After` header.

## Retrying Client

The API returns `Retry-After` when the user is rate limited. The sample client waits for that duration and retries.

```bash
python scripts/retry_client.py --user-id user-123 --attempts 3
```

This is intentionally client-side retry logic. It avoids holding server worker threads while waiting for a user's rate-limit window to reopen.

## Redis Design

When `RATE_LIMIT_BACKEND=redis`, the service uses Redis sorted sets:

- one sorted set per user for accepted request timestamps
- one hash per user for stats
- one set containing known users for `/stats`

The rate-limit check runs inside a Redis Lua script. Redis executes that script atomically, so two parallel API servers cannot both accept a request after observing the same remaining slot.

## Limitations

The default implementation uses Redis because the bonus requirement asks for Redis or a database. An in-memory backend is still available for tests or simple single-process demos by setting `RATE_LIMIT_BACKEND=memory`.

Important production limitations:

- There is no authentication, so `user_id` is trusted input.
- Redis stats currently expire after 7 days to avoid unbounded growth.
- If `RATE_LIMIT_BACKEND=memory` is used, data resets on restart and is not shared across workers or servers.

## What I Would Improve With More Time

- Add authentication and derive `user_id` from a verified token instead of trusting the request body.
- Add stronger cleanup policies for inactive users.
- Add OpenTelemetry metrics and structured JSON logs.
- Add a proper background queue for clients that prefer delayed processing over immediate rejection.
- Add CI with linting, type checking, and automated tests.

## Deploy Without A Credit Card

Azure commonly asks for a card, so use one of these instead.

### Recommended: Render Free Web Service

Render has a free web service tier for Python apps. It also has a free Key Value service that is Redis-compatible, so this is the best no-card option for showing both the API and the Redis bonus.

Limitations: free services can spin down when idle, and usage limits can change. This is fine for an assignment demo, not production.

### 1. Create a GitHub repository

```bash
git init
git add .
git commit -m "Initial rate limited API service"
git branch -M main
git remote add origin https://github.com/<your-username>/<repo-name>.git
git push -u origin main
```

### 2. Deploy the API on Render

1. Go to `https://render.com`.
2. Sign up with GitHub.
3. Click `New` -> `Web Service`.
4. Connect your GitHub repository.
5. Use these settings:
   - Runtime: `Docker`
   - Instance type: `Free`
   - Branch: `main`
   - Root directory: leave empty
6. Add environment variables:
   - `RATE_LIMIT_BACKEND=memory`
   - `RATE_LIMIT_MAX_REQUESTS=5`
   - `RATE_LIMIT_WINDOW_SECONDS=60`
7. Click `Create Web Service`.

After deploy, open:

```text
https://<your-render-service>.onrender.com/docs
```

### 3. Add Redis-Compatible Storage On Render

1. In Render, click `New` -> `Key Value`.
2. Choose the `Free` instance type.
3. Put it in the same region as the web service.
4. After it is created, copy the internal Redis URL.
5. Open your web service -> `Environment`.
6. Set:
   - `RATE_LIMIT_BACKEND=redis`
   - `REDIS_URL=<internal Redis URL from Render Key Value>`
7. Save changes and redeploy.

Render Key Value is Valkey-based but Redis-compatible, so the existing `redis` Python client works.

### Alternative: Hugging Face Spaces

Hugging Face Spaces is used for the live demo:

```text
https://huggingface.co/spaces/AndrewJerryV/rate-limited-api
```

API docs:

```text
https://AndrewJerryV-rate-limited-api.hf.space/docs
```

Hugging Face Spaces is a no-credit-card-friendly option for a Docker API demo. It is good for showing `/docs`, but it does not include a free managed Redis service. Because this project defaults to Redis, you must provide an external Redis-compatible URL in Space settings.

Steps:

1. Go to `https://huggingface.co/spaces`.
2. Click `Create new Space`.
3. Choose:
   - SDK: `Docker`
   - Visibility: Public
4. Push this repository to the Space repository.
5. Replace the Space README front matter with the contents of `HUGGINGFACE_SPACE_README.md`.
6. Keep these environment variables in Space settings:
   - `RATE_LIMIT_BACKEND=redis`
   - `REDIS_URL=<external Redis-compatible URL>`
   - `RATE_LIMIT_MAX_REQUESTS=5`
   - `RATE_LIMIT_WINDOW_SECONDS=60`

Open:

```text
https://AndrewJerryV-rate-limited-api.hf.space/docs
```

### Not Recommended For This Assignment

- Koyeb: currently documents card requirements in some flows, so avoid it if you have no card.
- Azure: usually asks for a card.
- Fly.io: commonly requires a card.

## Optional Docker Run

```bash
docker build -t rate-limited-api .
docker run --rm -p 8000:8000 rate-limited-api
```

Then open:

```text
http://127.0.0.1:8000/docs
```
