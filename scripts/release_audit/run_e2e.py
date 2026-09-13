"""按显式模式运行 Playwright E2E；不安装浏览器、不注入生产凭证。"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPEC = ROOT / "tests" / "e2e" / "release_core.spec.ts"


def main() -> int:
    parser = argparse.ArgumentParser(description="运行核心 E2E")
    parser.add_argument("--mode", choices=("local", "test", "production-readonly"), required=True)
    parser.add_argument("--spec", default=str(DEFAULT_SPEC))
    args = parser.parse_args()
    if args.mode == "production-readonly" and os.getenv("E2E_PRODUCTION_READONLY_ACK") != "true":
        print("e2e=refused reason=production_readonly_ack")
        return 1
    spec = Path(args.spec).resolve()
    if not spec.is_file() or ROOT not in spec.parents:
        print("e2e=failed reason=spec_missing_or_outside_repo")
        return 1
    base_url = os.getenv("E2E_BASE_URL", "").strip()
    if not base_url:
        print("e2e=failed reason=E2E_BASE_URL_missing")
        return 1
    npx_command = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx_command:
        print("e2e=failed reason=npx_missing")
        return 1
    env = os.environ.copy()
    env["E2E_MODE"] = args.mode
    for secret_name in (
        "APP_SECRET_KEY",
        "APP_ENCRYPTION_KEY",
        "API_KEY_PEPPER",
        "DATABASE_URL",
        "SMTP_PASSWORD",
        "TOMODD_UPSTREAM_TOKEN",
        "SMOKE_API_KEY",
        "SMOKE_SESSION_COOKIE",
    ):
        env.pop(secret_name, None)
    result = subprocess.run([npx_command, "--no-install", "playwright", "test", str(spec)], cwd=ROOT, env=env, check=False)
    print(f"e2e={'passed' if result.returncode == 0 else 'failed'} mode={args.mode}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
