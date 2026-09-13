"""验证 TLS 和反向代理的只读行为，不修改代理或证书配置。"""

from __future__ import annotations

import argparse
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlparse


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """保留重定向响应，便于拒绝 HTTPS 降级。"""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 HTTPS、证书校验和健康路径")
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    import os

    if os.getenv("RELEASE_TARGET_ENV") != "production":
        print("tls_proxy_validate=failed reason=RELEASE_TARGET_ENV")
        return 1
    parsed = urlparse(args.base_url.rstrip("/"))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        print("tls_proxy_validate=failed reason=https_required")
        return 1
    context = ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), _NoRedirect)
    request = urllib.request.Request(f"{args.base_url.rstrip('/')}/livez", method="GET", headers={"User-Agent": "release-audit-tls/1"})
    try:
        with opener.open(request, timeout=10) as response:
            status = int(response.status)
            location = response.headers.get("Location", "")
    except urllib.error.HTTPError as error:
        status = int(error.code)
        location = error.headers.get("Location", "")
    except (OSError, urllib.error.URLError):
        print("tls_proxy_validate=failed reason=connection_or_certificate")
        return 1
    downgrade = urlparse(location).scheme == "http" if location else False
    passed = status == 200 and not downgrade
    print(f"https_livez={'ok' if status == 200 else 'failed'} status={status}")
    print(f"redirect_policy={'ok' if not downgrade else 'failed'}")
    print(f"tls_proxy_validate={'passed' if passed else 'failed'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
