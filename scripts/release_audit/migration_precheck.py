"""迁移前只读检查；要求外部备份系统先提供已验证状态。"""

from __future__ import annotations

import os
import subprocess
import sys
import hashlib
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine, inspect, select


ROOT = Path(__file__).resolve().parents[2]
PYTHON = next(
    (candidate for candidate in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python") if candidate.exists()),
    ROOT / ".venv" / "Scripts" / "python.exe",
)
EXPECTED_HEAD = os.getenv("EXPECTED_ALEMBIC_HEAD", "a6b7c8d9e0f1")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")


def _username_key(value: object) -> str:
    """以升级后唯一键规则计算用户名规范键，兼容 Unicode 等价形式。"""

    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _username_problem(value: object, key: str) -> str | None:
    if not key:
        return "empty"
    if len(key) > 64:
        return "too_long"
    if _CONTROL_CHARACTER.search(str(value or "")):
        return "control_character"
    return None


def inspect_username_conflicts(database_url: str) -> list[dict[str, object]]:
    """只读扫描旧 users 表，不返回原始用户名、邮箱或用户标识。"""

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        if not inspector.has_table("users"):
            return []
        users = Table("users", MetaData(), autoload_with=engine)
        if "username" not in users.c:
            return [{"kind": "schema_invalid", "count": 1, "active_count": 0, "inactive_count": 0, "key_digest": ""}]
        columns = [users.c.username]
        if "is_active" in users.c:
            columns.append(users.c.is_active)
        rows = engine.connect().execute(select(*columns)).all()
    finally:
        engine.dispose()

    groups: dict[str, list[bool]] = defaultdict(list)
    invalid: dict[str, int] = defaultdict(int)
    for row in rows:
        username = row[0]
        key = _username_key(username)
        problem = _username_problem(username, key)
        active = bool(row[1]) if len(row) > 1 else True
        if problem:
            invalid[problem] += 1
        else:
            groups[key].append(active)

    conflicts: list[dict[str, object]] = []
    for key, states in groups.items():
        if len(states) > 1:
            conflicts.append(
                {
                    "kind": "duplicate_normalized_key",
                    "count": len(states),
                    "active_count": sum(states),
                    "inactive_count": len(states) - sum(states),
                    "key_digest": hashlib.sha256(key.encode("utf-8")).hexdigest()[:12],
                }
            )
    for kind, count in invalid.items():
        conflicts.append({"kind": f"invalid_{kind}", "count": count, "active_count": 0, "inactive_count": 0, "key_digest": ""})
    return sorted(conflicts, key=lambda item: (str(item["kind"]), str(item["key_digest"])))


def format_username_conflict_report(conflicts: list[dict[str, object]]) -> str:
    """生成不含原始用户名的冲突摘要，供人工选择处理方案。"""

    lines = ["用户名迁移前冲突报告（不含原始用户名）"]
    for item in conflicts:
        digest = str(item.get("key_digest") or "-")
        lines.append(
            "类型={kind} 数量={count} 活跃={active} 非活跃={inactive} 规范键摘要={digest}".format(
                kind=item["kind"],
                count=item["count"],
                active=item["active_count"],
                inactive=item["inactive_count"],
                digest=digest,
            )
        )
    return "\n".join(lines)


def ensure_no_username_conflicts(database_url: str) -> None:
    """在任何 Alembic 写入前拒绝旧库冲突，避免静默选择或部分升级。"""

    conflicts = inspect_username_conflicts(database_url)
    if conflicts:
        print("username_conflict_check=failed")
        print(format_username_conflict_report(conflicts))
        raise RuntimeError("发现用户名规范化冲突；请按冲突摘要完成业务决策后重新执行迁移")
    print("username_conflict_check=ok")


def _run(*args: str) -> tuple[int, str]:
    result = subprocess.run(
        [str(PYTHON), "-m", "alembic", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        env=os.environ.copy(),
    )
    output = (result.stdout or b"") + (result.stderr or b"")
    return result.returncode, output.decode("utf-8", errors="replace")


def main() -> int:
    """不执行升级，只确认目标、备份和迁移链条件。"""

    if os.getenv("RELEASE_TARGET_ENV") != "production":
        print("migration_precheck=failed reason=RELEASE_TARGET_ENV")
        return 1
    if os.getenv("BACKUP_VERIFIED", "").lower() != "true" or not os.getenv("BACKUP_VERIFICATION_ID", "").strip():
        print("migration_precheck=failed reason=backup_not_verified")
        return 1
    if not PYTHON.exists():
        print("migration_precheck=failed reason=project_venv_missing")
        return 1

    from app.config import get_settings

    try:
        ensure_no_username_conflicts(get_settings().database_url)
    except Exception:
        print("migration_precheck=failed reason=username_conflict")
        return 1

    heads_code, heads_output = _run("heads")
    current_code, current_output = _run("current")
    head_ok = heads_code == 0 and EXPECTED_HEAD in heads_output
    current_ok = current_code == 0
    print(f"alembic_heads={'ok' if head_ok else 'failed'}")
    print(f"alembic_current={'ok' if current_ok else 'failed'}")
    print(f"expected_head_configured={'ok' if bool(EXPECTED_HEAD) else 'failed'}")
    passed = head_ok and current_ok and bool(EXPECTED_HEAD)
    print(f"migration_precheck={'passed' if passed else 'failed'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
