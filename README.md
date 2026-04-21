# Rate-Limited API Service

Production-style FastAPI service for the Source Asia assignment.

It exposes:

- `POST /request` - accepts `{ "user_id": "...", "payload": ... }`
- `GET /stats` - returns per-user request statistics
- `GET /` - service summary
- `GET /health` - health check

The service enforces a per-user rate limit of **5 accepted requests per rolling 60-second window**.

## Bonus Features Included

- Redis-backed rate limiter using an atomic Lua script.
- In-memory fallback for simple local runs.
- `Retry-After` response header for rate-limited clients.
- Retrying client script in `scripts/retry_client.py`.
- Dockerfile and Docker Compose setup.
- Azure Container Apps deployment steps.

## Tech Stack

- Python 3.11+
- FastAPI
- Uvicorn
- Redis optional
- Pytest
- HTTPX

## Run Locally

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
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

Redis is the production-considerate bonus backend. It keeps rate limits accurate across multiple app processes and multiple deployed instances.

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
set RATE_LIMIT_BACKEND=redis
set REDIS_URL=redis://localhost:6379/0
uvicorn app.main:app --reload
```

PowerShell version:

```powershell
$env:RATE_LIMIT_BACKEND="redis"
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

The default implementation is intentionally in-memory because the assignment says a database is not required. The Redis backend is included for the bonus requirement.

Important production limitations:

- Data resets when the process restarts.
- In-memory state is not shared across multiple app instances.
- Running Uvicorn/Gunicorn with multiple worker processes would create one limiter per process, so the global per-user limit would not be accurate.
- There is no authentication, so `user_id` is trusted input.
- Old inactive users are kept in memory until the process restarts.
- Redis stats currently expire after 7 days to avoid unbounded growth.

## What I Would Improve With More Time

- Add authentication and derive `user_id` from a verified token instead of trusting the request body.
- Add stronger cleanup policies for inactive users.
- Add OpenTelemetry metrics and structured JSON logs.
- Add a proper background queue for clients that prefer delayed processing over immediate rejection.
- Add CI with linting, type checking, and automated tests.

## Deploy On Azure Container Apps

These are the outside steps to deploy after pushing this folder to GitHub.

### 1. Create a GitHub repository

```bash
git init
git add .
git commit -m "Initial rate limited API service"
git branch -M main
git remote add origin https://github.com/<your-username>/<repo-name>.git
git push -u origin main
```

### 2. Install and configure Azure CLI

```bash
az login
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
```

### 3. Deploy the API without Redis

This is the simplest cloud deployment and still satisfies the main assignment.

```bash
az containerapp up ^
  --name source-asia-rate-api ^
  --resource-group source-asia-rate-rg ^
  --location eastus ^
  --source . ^
  --ingress external ^
  --target-port 8000 ^
  --env-vars RATE_LIMIT_BACKEND=memory RATE_LIMIT_MAX_REQUESTS=5 RATE_LIMIT_WINDOW_SECONDS=60 ^
  --query properties.configuration.ingress.fqdn
```

Open the returned URL:

```text
https://<returned-fqdn>/docs
```

### 4. Deploy with Azure Redis

For the full bonus deployment, create a managed Redis instance and pass its connection URL as a Container Apps secret.

```bash
az group create --name source-asia-rate-rg --location eastus
az redis create ^
  --name <unique-redis-name> ^
  --resource-group source-asia-rate-rg ^
  --location eastus ^
  --sku Basic ^
  --vm-size c0
```

Get Redis connection details:

```bash
az redis show ^
  --name <unique-redis-name> ^
  --resource-group source-asia-rate-rg ^
  --query hostName ^
  --output tsv

az redis list-keys ^
  --name <unique-redis-name> ^
  --resource-group source-asia-rate-rg ^
  --query primaryKey ^
  --output tsv
```

Create the Redis URL:

```text
rediss://:<primary-key>@<redis-hostname>:6380/0
```

Deploy the app:

```bash
az containerapp up ^
  --name source-asia-rate-api ^
  --resource-group source-asia-rate-rg ^
  --location eastus ^
  --source . ^
  --ingress external ^
  --target-port 8000
```

Add the Redis secret and enable the Redis backend:

```bash
az containerapp secret set ^
  --name source-asia-rate-api ^
  --resource-group source-asia-rate-rg ^
  --secrets redis-url="rediss://:<primary-key>@<redis-hostname>:6380/0"

az containerapp update ^
  --name source-asia-rate-api ^
  --resource-group source-asia-rate-rg ^
  --set-env-vars RATE_LIMIT_BACKEND=redis REDIS_URL=secretref:redis-url RATE_LIMIT_MAX_REQUESTS=5 RATE_LIMIT_WINDOW_SECONDS=60
```

Check the deployed API:

```bash
curl https://<returned-fqdn>/health
curl https://<returned-fqdn>/stats
```

## Optional Docker Run

```bash
docker build -t rate-limited-api .
docker run --rm -p 8000:8000 rate-limited-api
```

Then open:

```text
http://127.0.0.1:8000/docs
```
