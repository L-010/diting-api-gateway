"""发布后只读冒烟；不执行写入、删除、注册或第三方副作用操作。"""

from __future__ import annotations

import argparse
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse


def _get(base: str, path: str, headers: dict[str, str] | None = None) -> int:
    request = urllib.request.Request(f"{base}{path}", method="GET", headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)
    except (OSError, urllib.error.URLError):
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="执行发布后核心 API 只读冒烟")
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    if os.getenv("RELEASE_TARGET_ENV") != "production" or os.getenv("RELEASE_VALIDATION_ACK") != "I_UNDERSTAND_TARGET_ENV":
        print("post_release_smoke=failed reason=target_ack")
        return 1
    base = args.base_url.rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        print("post_release_smoke=failed reason=invalid_base_url")
        return 1
    checks = {
        "livez": _get(base, "/livez"),
        "readyz": _get(base, "/readyz"),
        "public_config": _get(base, "/api/public/config"),
        "public_tools": _get(base, "/api/public/tools"),
        "unauthenticated_me": _get(base, "/api/me"),
    }
    session_cookie = os.getenv("SMOKE_SESSION_COOKIE", "").strip()
    if session_cookie:
        checks["authenticated_me"] = _get(base, "/api/me", {"Cookie": session_cookie})
    readonly_path = os.getenv("SMOKE_READONLY_PATH", "").strip()
    if readonly_path:
        if not readonly_path.startswith("/") or any(token in readonly_path for token in ("?", "#")):
            print("post_release_smoke=failed reason=invalid_readonly_path")
            return 1
        api_key = os.getenv("SMOKE_API_KEY", "").strip()
        if not api_key:
            print("post_release_smoke=failed reason=SMOKE_API_KEY_missing")
            return 1
        checks["configured_readonly_api"] = _get(base, readonly_path, {"X-API-Key": api_key})
    expected = {"livez": 200, "readyz": 200, "public_config": 200, "public_tools": 200, "unauthenticated_me": 401}
    if session_cookie:
        expected["authenticated_me"] = 200
    if readonly_path:
        expected["configured_readonly_api"] = 200
    for name, status in checks.items():
        print(f"{name}={'ok' if status == expected[name] else 'failed'} status={status}")
    passed = all(checks[name] == expected[name] for name in checks)
    print(f"post_release_smoke={'passed' if passed else 'failed'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
