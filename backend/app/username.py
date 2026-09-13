"""用户名展示值与持久化规范键的统一规则。"""

from __future__ import annotations

import unicodedata


def normalize_username_key(value: object) -> str:
    """按 Unicode NFKC、去首尾空白和不区分大小写规则生成唯一键。"""

    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
