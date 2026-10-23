"""Wait until a Cosmos DB endpoint (normally the Linux emulator in CI) answers real SDK requests.

The vnext emulator accepts TLS connections before its data plane is up and answers
"503 ServiceUnavailable: pgcosmos extension is still starting" for a while, so probing the port is
not enough. This script retries the same calls the live tests start with (client construction and
listing databases) until they succeed or the timeout expires.

Usage: ARCVIZ_TEST_COSMOS_URL=... ARCVIZ_TEST_COSMOS_KEY=... [ARCVIZ_TEST_COSMOS_INSECURE=1] \
       python scripts/wait_for_cosmos.py [--timeout SECONDS]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import warnings


def ready(url: str, key: str, verify: bool, budget: float) -> None:
    """One probe, bounded by `budget` seconds: no SDK-internal retries, and every HTTP timeout capped
    by the remaining budget, so a stalled endpoint cannot extend the step beyond --timeout."""
    from azure.cosmos import CosmosClient

    budget = max(1.0, budget)
    client = CosmosClient(
        url,
        credential=key,
        connection_verify=verify,
        retry_total=0,
        connection_timeout=min(10.0, budget),
        read_timeout=budget,
        timeout=budget,
    )
    list(client.list_databases(timeout=budget))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=float, default=180.0, help="seconds to keep trying (default 180)")
    parser.add_argument("--interval", type=float, default=3.0, help="seconds between attempts (default 3)")
    args = parser.parse_args()

    url = os.environ.get("ARCVIZ_TEST_COSMOS_URL")
    key = os.environ.get("ARCVIZ_TEST_COSMOS_KEY")
    if not url or not key:
        print("ARCVIZ_TEST_COSMOS_URL and ARCVIZ_TEST_COSMOS_KEY must be set", file=sys.stderr)
        return 2
    verify = os.environ.get("ARCVIZ_TEST_COSMOS_INSECURE") != "1"
    if not verify:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    deadline = time.monotonic() + args.timeout
    attempt = 0
    last_error = "no attempt made"
    while True:
        attempt += 1
        try:
            ready(url, key, verify, deadline - time.monotonic())
        except Exception as exc:  # any failure means "not ready yet"; the last one is reported on timeout
            last_error = f"{type(exc).__name__}: {str(exc).strip().splitlines()[0] if str(exc).strip() else exc!r}"
        else:
            print(f"Cosmos DB at {url} is ready (attempt {attempt}).")
            return 0
        if time.monotonic() >= deadline:
            print(
                f"Cosmos DB at {url} not ready after {args.timeout:.0f}s ({attempt} attempts): {last_error}",
                file=sys.stderr,
            )
            return 1
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
