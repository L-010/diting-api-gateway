"""只读 HTTP 性能探针；不把 SQLite 微基准解释为生产容量结论。"""

from __future__ import annotations

import argparse
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse


def _request(base: str, path: str) -> tuple[float, int]:
    started = time.perf_counter()
    request = urllib.request.Request(f"{base}{path}", method="GET", headers={"User-Agent": "release-audit-performance/1"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status = int(response.status)
    except urllib.error.HTTPError as error:
        status = int(error.code)
    except (OSError, urllib.error.URLError):
        status = 0
    return (time.perf_counter() - started) * 1000, status


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description="运行只读性能探针")
    parser.add_argument("--mode", choices=("local", "test", "production-readonly"), required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--path", default="/livez")
    parser.add_argument("--requests", type=int, default=int(os.getenv("PERF_REQUESTS", "100")))
    parser.add_argument("--concurrency", type=int, default=int(os.getenv("PERF_CONCURRENCY", "5")))
    args = parser.parse_args()
    if args.mode == "production-readonly" and os.getenv("PERF_PRODUCTION_READONLY_ACK") != "true":
        print("performance=refused reason=production_readonly_ack")
        return 1
    parsed = urlparse(args.base_url.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not args.path.startswith("/"):
        print("performance=failed reason=invalid_target")
        return 1
    if args.requests < 1 or args.requests > 100_000 or args.concurrency < 1 or args.concurrency > 500:
        print("performance=failed reason=parameters_out_of_bounds")
        return 1
    started = time.perf_counter()
    durations: list[float] = []
    statuses: list[int] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(_request, args.base_url.rstrip("/"), args.path) for _ in range(args.requests)]
        for future in as_completed(futures):
            duration, status = future.result()
            durations.append(duration)
            statuses.append(status)
    elapsed = max(time.perf_counter() - started, 0.001)
    errors = sum(1 for status in statuses if status < 200 or status >= 300)
    p95 = _percentile(durations, 0.95)
    p99 = _percentile(durations, 0.99)
    throughput = len(durations) / elapsed
    print(f"requests={len(durations)} concurrency={args.concurrency} throughput_rps={throughput:.2f}")
    print(f"p50_ms={_percentile(durations, 0.50):.2f} p95_ms={p95:.2f} p99_ms={p99:.2f} error_rate={errors / len(statuses):.4f}")
    threshold_p95 = float(os.getenv("PERF_P95_MS", "0"))
    threshold_p99 = float(os.getenv("PERF_P99_MS", "0"))
    threshold_error = float(os.getenv("PERF_ERROR_RATE", "0"))
    threshold_rps = float(os.getenv("PERF_THROUGHPUT_RPS", "0"))
    thresholds_configured = threshold_p95 > 0 and threshold_p99 > 0 and threshold_rps > 0 and threshold_error >= 0
    passed = thresholds_configured and p95 <= threshold_p95 and p99 <= threshold_p99 and throughput >= threshold_rps and errors / len(statuses) <= threshold_error
    print(f"thresholds={'configured' if thresholds_configured else 'missing'}")
    print(f"performance={'passed' if passed else 'failed'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
