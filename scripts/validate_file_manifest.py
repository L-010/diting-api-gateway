"""校验工具输出的 Manifest v1，供工具封装和 CI 使用。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from datetime import datetime


ROLES = {"input", "intermediate", "output", "log", "archive"}
VISIBILITIES = {"user", "admin", "internal"}
STATUSES = {"pending", "ready", "delete_pending", "deleted", "expired", "missing", "quarantined", "error"}


def _datetime_valid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _validate(payload: object) -> list[str]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(payload.get("artifacts"), list):
        return ["$: 必须包含 schema_version=1 和 artifacts 数组"]
    artifacts = payload["artifacts"]
    if len(artifacts) > 1000:
        return ["artifacts: 文件数超过 1000 项"]
    errors: list[str] = []
    for index, item in enumerate(artifacts):
        prefix = f"artifacts.{index}"
        if not isinstance(item, dict):
            errors.append(f"{prefix}: 必须是对象")
            continue
        file_id, name = item.get("file_id"), item.get("name")
        if not isinstance(file_id, str) or not file_id or len(file_id) > 255 or any(ord(char) < 32 or ord(char) == 127 for char in file_id):
            errors.append(f"{prefix}.file_id: 必须是 1 到 255 字符的稳定不透明 ID，且不能含控制字符")
        if not isinstance(name, str) or not name or len(name) > 255:
            errors.append(f"{prefix}.name: 必须是 1 到 255 字符的文件名")
        if item.get("role") not in ROLES:
            errors.append(f"{prefix}.role: 角色不受支持")
        if item.get("visibility") not in VISIBILITIES:
            errors.append(f"{prefix}.visibility: 可见性不受支持")
        if item.get("status") not in STATUSES:
            errors.append(f"{prefix}.status: 状态不受支持")
        if not isinstance(item.get("size_bytes"), int) or isinstance(item.get("size_bytes"), bool) or item["size_bytes"] < 0:
            errors.append(f"{prefix}.size_bytes: 必须是非负整数")
        if not _datetime_valid(item.get("created_at")):
            errors.append(f"{prefix}.created_at: 必须是 ISO 8601 时间")
        checksum = item.get("sha256")
        if checksum is not None and (not isinstance(checksum, str) or len(checksum) != 64 or any(char not in "0123456789abcdefABCDEF" for char in checksum)):
            errors.append(f"{prefix}.sha256: 必须是 64 位十六进制字符串")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: python scripts/validate_file_manifest.py <manifest.json>", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    json.loads((root / "docs" / "schemas" / "remote-file-manifest-v1.schema.json").read_text(encoding="utf-8"))
    path = Path(sys.argv[1]).resolve()
    payload_bytes = path.read_bytes()
    if len(payload_bytes) > 1_048_576:
        print("Manifest 超过 1 MiB 平台上限", file=sys.stderr)
        return 1
    payload = json.loads(payload_bytes.decode("utf-8"))
    errors = _validate(payload)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Manifest v1 校验通过，共 {len(payload['artifacts'])} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
