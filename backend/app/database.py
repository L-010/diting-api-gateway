"""SQLAlchemy 连接和会话管理。"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    """所有持久化模型的基类。"""


def _connect_args() -> dict[str, object]:
    return {"check_same_thread": False} if get_settings().database_url.startswith("sqlite") else {}


def _engine_options() -> dict[str, object]:
    """为服务型数据库启用连接存活检查，避免复用已被服务端回收的连接。"""

    if get_settings().database_url.startswith("sqlite"):
        return {}
    return {"pool_pre_ping": True, "pool_recycle": 1800}


engine = create_engine(
    get_settings().database_url,
    connect_args=_connect_args(),
    future=True,
    **_engine_options(),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def initialize_database() -> None:
    """仅供隔离测试库建表；实际运行必须通过 Alembic 迁移。"""
    from . import models

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
