"""Checks route throttling on AWS: sends requests faster than a route's limit (api.tf,
`api_throttle_rate` and `api_throttle_burst`) and counts the answers; above it API Gateway answers
429 before any Lambda runs.

    cd infra
    uv run python scripts/throttle_check.py     # 80 requests to GET /agents, 8 at a time

No API key is sent: admitted requests reach the control plane and answer 401 (a few Lambda
invocations, within the free tier). Measured on dev (2026-11-20):
- Unsigned requests to an IAM route (`/internal/*`) are refused with 403 and never counted, so
  they cannot show throttling.
- One burst of 30 at once got no 429 (throttling is best-effort and spread over API Gateway's
  hosts), but 7 answers of 503: more than the account's 10 concurrent Lambdas.
So the check sends a steady stream, at most `--concurrency` at a time (keep it under 10). Exit
code 0 when at least one 429 came back. Reads the API address from `terraform output`; no AWS
credentials used.
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

INFRA = Path(__file__).resolve().parent.parent


def api_url() -> str:
    raw = subprocess.run(
        ["terraform", f"-chdir={INFRA / 'envs' / 'dev'}", "output", "-json", "api_url"],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(raw).rstrip("/")


def status(url: str) -> int:
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return 0  # no HTTP answer (connection error, timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", default="/agents")
    parser.add_argument("--requests", type=int, default=80)
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()

    url = api_url() + args.path
    print(f"{args.requests} requests to {args.path}, {args.concurrency} at a time")
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        answers = Counter(pool.map(lambda _: status(url), range(args.requests)))
    seconds = time.monotonic() - started
    print(f"   in {seconds:.1f} s ({args.requests / seconds:.0f} requests per second)")
    for code, count in sorted(answers.items()):
        print(f"   {code or 'no answer'}: {count}")
    if answers[429]:
        print(f"Throttled: {answers[429]} of {args.requests} got 429")
        return 0
    print("No 429: the stream stayed under the limit (more --requests?) or throttling is off")
    return 1


if __name__ == "__main__":
    sys.exit(main())
