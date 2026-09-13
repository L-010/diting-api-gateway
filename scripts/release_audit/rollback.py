"""回滚编排检查器；不自动降级数据库、不删除数据、不停止非本脚本进程。"""

from __future__ import annotations

import argparse
import os


def main() -> int:
    parser = argparse.ArgumentParser(description="确认应用回滚前置条件并输出人工步骤编号")
    parser.add_argument("--confirm", action="store_true", help="确认已由责任人批准回滚")
    args = parser.parse_args()
    checks = {
        "target": os.getenv("RELEASE_TARGET_ENV") == "production",
        "ack": os.getenv("RELEASE_VALIDATION_ACK") == "I_UNDERSTAND_TARGET_ENV",
        "approval": os.getenv("ROLLBACK_APPROVED") == "true",
        "backup": os.getenv("BACKUP_VERIFIED", "").lower() == "true",
        "confirm_flag": args.confirm,
    }
    for name, ok in checks.items():
        print(f"{name}={'ok' if ok else 'failed'}")
    if not all(checks.values()):
        print("rollback=refused")
        return 1
    print("rollback=manual_steps_required")
    print("step=1 从受控制品仓库选择上一版本应用制品")
    print("step=2 将流量切换到上一版本并观察 livez/readyz")
    print("step=3 数据库仅允许执行已批准的前向兼容方案，禁止自动 downgrade")
    print("step=4 执行只读冒烟并记录回滚证据")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
