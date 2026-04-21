import argparse
import time
from typing import Any

import httpx


def post_with_retry(
    url: str,
    body: dict[str, Any],
    attempts: int,
) -> httpx.Response:
    for attempt in range(1, attempts + 1):
        response = httpx.post(url, json=body, timeout=10)
        if response.status_code != 429:
            return response

        retry_after = int(response.headers.get("Retry-After", "1"))
        if attempt == attempts:
            return response

        print(
            f"Rate limited. Waiting {retry_after}s before retry "
            f"{attempt + 1}/{attempts}."
        )
        time.sleep(retry_after)

    return response


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrying client for /request.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--user-id", default="alice")
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args()

    response = post_with_retry(
        url=f"{args.base_url.rstrip('/')}/request",
        body={"user_id": args.user_id, "payload": {"source": "retry_client"}},
        attempts=args.attempts,
    )

    print(response.status_code)
    print(response.text)


if __name__ == "__main__":
    main()
