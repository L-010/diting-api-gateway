"""由环境变量创建初始管理员，绝不输出或重置密码。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select

from app.database import SessionLocal
from app.main import seed_roles
from app.models import ApprovalStatus, Role, RoleName, User, UserRole
from app.services.security import hash_password, validate_password


def main() -> int:
    username = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "").strip()
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not username or not password:
        print("需要设置 BOOTSTRAP_ADMIN_USERNAME 和 BOOTSTRAP_ADMIN_PASSWORD。", file=sys.stderr)
        return 2
    try:
        validate_password(password, username)
    except ValueError as error:
        print(f"管理员密码不符合要求: {error}", file=sys.stderr)
        return 2
    seed_roles()
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.username == username))
        admin_role = session.scalar(select(Role).where(Role.name == RoleName.ADMIN.value))
        if not admin_role:
            print("管理员角色未初始化。", file=sys.stderr)
            return 1
        if not user:
            user = User(
                username=username,
                password_hash=hash_password(password),
                is_active=True,
                approval_status=ApprovalStatus.APPROVED.value,
                must_change_password=False,
            )
            session.add(user)
            session.flush()
        else:
            user.is_active = True
            user.approval_status = ApprovalStatus.APPROVED.value
            user.rejection_reason = ""
        has_admin_role = session.scalar(select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == admin_role.id))
        if not has_admin_role:
            session.add(UserRole(user_id=user.id, role_id=admin_role.id))
        session.commit()
    print(f"管理员账号 {username} 已就绪。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
