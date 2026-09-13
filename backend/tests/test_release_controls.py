from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app import main
from app.database import Base


def test_livez_is_process_only() -> None:
    response = main.livez()
    assert response == {"status": "ok"}


def test_readyz_requires_database_migration_head(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version(version_num) VALUES ('a6b7c8d9e0f1')"))
    monkeypatch.setattr(main, "SessionLocal", lambda: Session(engine))
    response = main.readyz()
    assert response.status_code == 200


def test_readyz_fails_closed_without_migration_table(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(main, "SessionLocal", lambda: Session(engine))
    response = main.readyz()
    assert response.status_code == 503


def test_readyz_fails_closed_when_database_connection_is_unavailable(monkeypatch) -> None:
    """数据库连接异常不能被就绪检查误报为可接流量。"""

    def unavailable_session():
        raise RuntimeError("synthetic database unavailable")

    monkeypatch.setattr(main, "SessionLocal", unavailable_session)
    response = main.readyz()
    assert response.status_code == 503
    assert response.body == b'{"status":"not_ready","checks":{"config":"ok","database":"failed","migration":"failed"}}'
