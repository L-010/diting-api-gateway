"""将当前版本的 SQLite 数据完整迁移到空 MySQL 数据库并执行一致性校验。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, Table, and_, create_engine, func, inspect, select
from sqlalchemy.engine import Connection, Engine


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend"))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将 SQLite 数据迁移到空 MySQL 数据库")
    parser.add_argument(
        "--source-url",
        default=f"sqlite:///{(ROOT_DIR / 'data' / 'api_gateway.db').as_posix()}",
        help="SQLite 源数据库 URL，默认使用 data/api_gateway.db",
    )
    parser.add_argument(
        "--target-url-env",
        default="MYSQL_DATABASE_URL",
        help="保存 MySQL 目标 URL 的环境变量名，默认 MYSQL_DATABASE_URL",
    )
    parser.add_argument("--prepare-target", action="store_true", help="先在目标空库执行 Alembic 升级")
    parser.add_argument("--batch-size", type=int, default=500, help="每批写入行数，默认 500")
    return parser.parse_args()


def _validate_urls(source_url: str, target_url: str) -> None:
    source_scheme = urlparse(source_url).scheme
    target_scheme = urlparse(target_url).scheme
    if not source_scheme.startswith("sqlite"):
        raise RuntimeError("源数据库必须是 SQLite")
    if target_scheme not in {"mysql", "mysql+pymysql", "mariadb", "mariadb+pymysql"}:
        raise RuntimeError("目标数据库必须是使用 PyMySQL 的 MySQL 或 MariaDB")
    if source_url == target_url:
        raise RuntimeError("源数据库与目标数据库不能相同")


def _validate_sqlite_file(source_url: str) -> None:
    parsed = urlparse(source_url)
    raw_path = unquote(parsed.path)
    if os.name == "nt" and raw_path.startswith("/"):
        raw_path = raw_path[1:]
    source_path = Path(raw_path)
    if not source_path.is_file():
        raise RuntimeError(f"SQLite 源数据库不存在: {source_path}")


def _alembic_head() -> str:
    config = Config(str(ROOT_DIR / "alembic.ini"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"Alembic 必须只有一个 head，当前数量: {len(heads)}")
    return heads[0]


def _database_revision(engine: Engine) -> str | None:
    inspector = inspect(engine)
    if not inspector.has_table("alembic_version"):
        return None
    with engine.connect() as connection:
        rows = connection.execute(select(Table("alembic_version", MetaData(), autoload_with=engine).c.version_num)).scalars().all()
    if len(rows) != 1:
        raise RuntimeError(f"alembic_version 必须恰好一行，当前数量: {len(rows)}")
    return str(rows[0])


def _prepare_target(target_url: str) -> None:
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = target_url
    try:
        from app.config import get_settings

        get_settings.cache_clear()
        command.upgrade(Config(str(ROOT_DIR / "alembic.ini")), "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        from app.config import get_settings

        get_settings.cache_clear()


def _application_tables() -> list[str]:
    from app import models
    from app.database import Base

    del models
    return [table.name for table in Base.metadata.sorted_tables]


def _ensure_schema(engine: Engine, expected_tables: list[str], expected_revision: str, label: str) -> None:
    revision = _database_revision(engine)
    if revision != expected_revision:
        raise RuntimeError(f"{label} Alembic 版本不一致: expected={expected_revision}, actual={revision or 'missing'}")
    actual = set(inspect(engine).get_table_names())
    missing = sorted(set(expected_tables) - actual)
    if missing:
        raise RuntimeError(f"{label} 缺少数据表: {', '.join(missing)}")


def _ensure_target_empty(connection: Connection, metadata: MetaData, table_names: list[str]) -> None:
    nonempty: list[str] = []
    for name in table_names:
        count = int(connection.scalar(select(func.count()).select_from(metadata.tables[name])) or 0)
        if count:
            nonempty.append(f"{name}={count}")
    if nonempty:
        raise RuntimeError("目标库不是空库，拒绝覆盖: " + ", ".join(nonempty))


def _normalized(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    return value


def _table_digest(connection: Connection, table: Table) -> tuple[int, str]:
    rows = []
    for row in connection.execute(select(table)).mappings():
        normalized = {column.name: _normalized(row[column.name]) for column in table.columns}
        rows.append(json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))
    rows.sort()
    digest = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
    return len(rows), digest


def _verify_foreign_keys(connection: Connection, metadata: MetaData, table_names: list[str]) -> None:
    problems: list[str] = []
    for table_name in table_names:
        table = metadata.tables[table_name]
        for constraint in table.foreign_key_constraints:
            child = table.alias("child")
            parent = constraint.referred_table.alias("parent")
            pairs = [(child.c[element.parent.name], parent.c[element.column.name]) for element in constraint.elements]
            join_condition = and_(*(left == right for left, right in pairs))
            child_present = and_(*(left.is_not(None) for left, _ in pairs))
            parent_missing = and_(*(right.is_(None) for _, right in pairs))
            count = int(
                connection.scalar(
                    select(func.count()).select_from(child.outerjoin(parent, join_condition)).where(child_present, parent_missing)
                )
                or 0
            )
            if count:
                problems.append(f"{table_name}.{constraint.name or 'unnamed'}={count}")
    if problems:
        raise RuntimeError("目标库存在外键孤儿记录: " + ", ".join(problems))


def _migrate(source: Engine, target: Engine, table_names: list[str], batch_size: int) -> None:
    source_metadata = MetaData()
    target_metadata = MetaData()
    source_metadata.reflect(bind=source, only=table_names)
    target_metadata.reflect(bind=target, only=table_names)

    with target.connect() as target_connection:
        _ensure_target_empty(target_connection, target_metadata, table_names)
        # 空库检查会触发 SQLAlchemy 自动事务，复制前显式结束该只读事务。
        target_connection.commit()
        transaction = target_connection.begin()
        try:
            target_connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
            with source.connect() as source_connection:
                for table_name in table_names:
                    source_table = source_metadata.tables[table_name]
                    target_table = target_metadata.tables[table_name]
                    result = source_connection.execute(select(source_table))
                    migrated = 0
                    while True:
                        batch = [dict(row) for row in result.mappings().fetchmany(batch_size)]
                        if not batch:
                            break
                        target_connection.execute(target_table.insert(), batch)
                        migrated += len(batch)
                    print(f"table={table_name} migrated={migrated}")
                for table_name in table_names:
                    source_count, source_digest = _table_digest(source_connection, source_metadata.tables[table_name])
                    target_count, target_digest = _table_digest(target_connection, target_metadata.tables[table_name])
                    if (source_count, source_digest) != (target_count, target_digest):
                        raise RuntimeError(
                            f"表校验失败: {table_name}, source={source_count}/{source_digest[:12]}, "
                            f"target={target_count}/{target_digest[:12]}"
                        )
            _verify_foreign_keys(target_connection, target_metadata, table_names)
            transaction.commit()
        except Exception:
            transaction.rollback()
            raise
        finally:
            target_connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")
            target_connection.commit()


def main() -> int:
    args = _arguments()
    if args.batch_size < 1 or args.batch_size > 10_000:
        raise RuntimeError("batch-size 必须在 1 到 10000 之间")
    target_url = os.environ.get(args.target_url_env, "").strip()
    if not target_url:
        raise RuntimeError(f"缺少目标数据库环境变量: {args.target_url_env}")
    _validate_urls(args.source_url, target_url)
    _validate_sqlite_file(args.source_url)

    source = create_engine(args.source_url, future=True)
    target = create_engine(target_url, future=True, pool_pre_ping=True)
    try:
        head = _alembic_head()
        tables = _application_tables()
        _ensure_schema(source, tables, head, "源库")
        if args.prepare_target:
            existing = set(inspect(target).get_table_names())
            if existing - {"alembic_version"}:
                raise RuntimeError("prepare-target 只允许用于全新的空 MySQL 数据库")
            _prepare_target(target_url)
        _ensure_schema(target, tables, head, "目标库")
        _migrate(source, target, tables, args.batch_size)
        print(f"migration=passed tables={len(tables)} alembic_head={head}")
        return 0
    finally:
        source.dispose()
        target.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
