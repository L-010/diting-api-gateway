"""只读预检本机 tomoDD-SP 服务，不创建、不修改、不删除上游资源。"""

from __future__ import annotations

import os
import sys
import time

import httpx


def main() -> int:
    base_url = os.environ.get("TOMODD_BASE_URL", "http://127.0.0.1:18000").rstrip("/")
    token = os.environ.get("TOMODD_UPSTREAM_TOKEN", "")
    retries = int(os.environ.get("TOMODD_VERIFY_RETRIES", "6"))
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            # 本地 Docker Desktop 端口必须直连，避免系统代理/TUN 把 127.0.0.1 请求错误转发。
            with httpx.Client(timeout=5, trust_env=False) as client:
                health = client.get(f"{base_url}/health")
                health.raise_for_status()
                info = client.get(f"{base_url}/api/v1/info", headers=headers)
            print(f"health={health.status_code} info={info.status_code}")
            try:
                health_status = health.json().get("status")
            except ValueError:
                print("tomoDD-SP 预检失败: /health 未返回合法 JSON", file=sys.stderr)
                return 1
            return 0 if health_status == "ok" and info.status_code < 400 else 1
        except httpx.HTTPError as error:
            last_error = error
            if attempt < retries:
                time.sleep(min(2, attempt * 0.5))
                continue
    print(f"tomoDD-SP 预检失败: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
