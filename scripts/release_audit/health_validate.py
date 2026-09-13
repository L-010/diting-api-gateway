"""目标环境健康检查；只读取健康接口并拒绝不安全的 URL。"""

from __future__ import annotations

import argparse
import os
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlparse


def _status(url: str) -> int:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "release-audit-health/1"})
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=10, context=context) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)
    except (OSError, urllib.error.URLError):
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="验证目标环境 livez、readyz 和 health")
    parser.add_argument("--base-url", required=True, help="不含路径的目标服务地址")
    args = parser.parse_args()
    if os.environ.get("RELEASE_TARGET_ENV") != "production":
        print("health_validate=failed reason=RELEASE_TARGET_ENV")
        return 1
    base = args.base_url.rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        print("health_validate=failed reason=invalid_base_url")
        return 1
    statuses = {path: _status(f"{base}{path}") for path in ("/livez", "/readyz", "/health")}
    for path, status in statuses.items():
        print(f"{path.lstrip('/')}={'ok' if status == 200 else 'failed'} status={status}")
    passed = all(status == 200 for status in statuses.values())
    print(f"health_validate={'passed' if passed else 'failed'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
