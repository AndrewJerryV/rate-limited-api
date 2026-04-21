---
title: Rate Limited API Service
emoji: 🚦
colorFrom: blue
colorTo: green
sdk: docker
app_port: 8000
---

# Rate-Limited API Service

FastAPI assignment service with:

- `POST /request`
- `GET /stats`
- `GET /health`
- `GET /docs`

Open `/docs` to test the API interactively.

Runtime variables required:

- `RATE_LIMIT_BACKEND=redis`
- `REDIS_URL=<external Redis-compatible URL>`
- `RATE_LIMIT_MAX_REQUESTS=5`
- `RATE_LIMIT_WINDOW_SECONDS=60`
