"""在系统临时副本中验证第七阶段关键回归测试能捕获对应变异。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

MUTATIONS = [
    (
        "路由顺序",
        "app/services/gateway_proxy.py",
        "candidates.sort(key=lambda item: (len(TEMPLATE_PARAM_PATTERN.findall(item.gateway_path)), -len(item.gateway_path)))",
        "candidates.sort(key=lambda item: 0)",
        "backend/tests/test_gateway_boundaries.py::test_静态路由优先于参数路由",
    ),
    (
        "用户名规范化",
        "app/models.py",
        "target.username_key = normalize_username_key(target.username)",
        "target.username_key = target.username.strip().lower()",
        "backend/tests/test_phase7_local_risks.py::test_username_key_rejects_unicode_normalization_equivalent_values",
    ),
    (
        "Worker 领取锁",
        "app/services/remote_files.py",
        "if result.rowcount:\n            session.expire(row)",
        "if True:\n            session.expire(row)",
        "backend/tests/test_local_logic_invariants.py::test_并发领取同一文件任务只返回一个持有者",
    ),
    (
        "幂等重放",
        "app/services/gateway_proxy.py",
        "if not claim.owner:\n                await wait_for_idempotency_result",
        "if False:\n                await wait_for_idempotency_result",
        "backend/tests/test_gateway_proxy_mock.py::test_proxy_reuses_idempotent_result_and_forwards_key_once",
    ),
    (
        "外部错误脱敏",
        "app/redaction.py",
        "return f\"{category}:{type(error).__name__}\"",
        "return str(error)",
        "backend/tests/test_phase7_local_risks.py::test_external_error_text_is_redacted_before_persistence_and_audit",
    ),
    (
        "迁移冲突检测",
        "scripts/release_audit/migration_precheck.py",
        "    engine = create_engine(database_url)",
        "    return []\n\n    engine = create_engine(database_url)",
        "backend/tests/test_phase7_local_risks.py::test_migration_precheck_reports_normalized_username_conflicts_without_full_names",
    ),
    (
        "权限拒绝",
        "app/routers/gateway.py",
        "    return (\n        \"*\" in scopes",
        "    return True\n    return (\n        \"*\" in scopes",
        "backend/tests/test_gateway_boundaries.py::test_api_key_scope_supports_dynamic_tool_and_legacy_tomodd",
    ),
]


def _copy_workspace(destination: Path) -> None:
    shutil.copytree(ROOT / "backend", destination / "backend")
    shutil.copytree(ROOT / "scripts", destination / "scripts")


def main() -> int:
    if not PYTHON.exists():
        raise RuntimeError("项目虚拟环境不存在")
    results: list[tuple[str, bool]] = []
    for name, relative_path, original, mutated, test_name in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix="phase7-mutation-") as temporary:
            workspace = Path(temporary)
            _copy_workspace(workspace)
            target = workspace / (Path("backend") / relative_path if relative_path.startswith("app/") else relative_path)
            content = target.read_text(encoding="utf-8")
            if original not in content:
                raise RuntimeError(f"变异定位失败：{name}")
            target.write_text(content.replace(original, mutated, 1), encoding="utf-8")
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(workspace / "backend")
            result = subprocess.run(
                [str(PYTHON), "-m", "pytest", test_name, "-p", "no:cacheprovider", "-q"],
                cwd=workspace,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            captured = result.returncode != 0
            results.append((name, captured))
            print(f"mutation_{name}={'captured' if captured else 'missed'}")
    return 0 if all(captured for _, captured in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
