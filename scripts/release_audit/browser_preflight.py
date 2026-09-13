"""检查本地 Node 依赖和 Playwright 浏览器状态，不自动安装任何内容。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


def _run(command: list[str], cwd: Path) -> tuple[int, str]:
    result = subprocess.run(command, cwd=cwd, check=False, capture_output=True)
    output = (result.stdout or b"") + (result.stderr or b"")
    return result.returncode, output.decode("utf-8", errors="replace").strip()


def main() -> int:
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    node_ok = shutil.which("node") is not None
    npm_ok = shutil.which("npm") is not None
    npx_command = shutil.which("npx") or shutil.which("npx.cmd")
    local_playwright = bool((FRONTEND / "node_modules" / ".bin" / "playwright.cmd").exists() or (FRONTEND / "node_modules" / ".bin" / "playwright").exists())
    dependency_names = set(package.get("dependencies", {})) | set(package.get("devDependencies", {}))
    declared = bool({"playwright", "@playwright/test"} & dependency_names)
    dry_code, _ = _run([npx_command, "--no-install", "playwright", "install", "--dry-run"], FRONTEND) if npx_command else (1, "")
    print(f"node={'ok' if node_ok else 'missing'}")
    print(f"npm={'ok' if npm_ok else 'missing'}")
    print(f"npx={'ok' if npx_command else 'missing'}")
    print(f"playwright_declared={'ok' if declared else 'missing'}")
    print(f"playwright_local={'ok' if local_playwright else 'missing'}")
    print(f"browser_dry_run={'ok' if dry_code == 0 else 'unavailable'}")
    passed = node_ok and npm_ok and bool(npx_command) and local_playwright and dry_code == 0
    print(f"browser_preflight={'passed' if passed else 'failed'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
