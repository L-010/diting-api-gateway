from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.database import Base, get_db
from app.main import app
from app.services.login_limit import reset_login_limit_state


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(get_settings(), "login_failure_limit", 3)
    monkeypatch.setattr(get_settings(), "login_protection_enabled", True)
    reset_login_limit_state()

    def override_database() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_database
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        test_client.close()
        app.dependency_overrides.clear()
        reset_login_limit_state()
        engine.dispose()


def test_unknown_username_login_is_limited_by_ip_and_username(client: TestClient) -> None:
    for _ in range(3):
        response = client.post("/api/auth/login", json={"username": "ghost", "password": "WrongPass2026!"})
        assert response.status_code == 401

    limited = client.post("/api/auth/login", json={"username": "ghost", "password": "WrongPass2026!"})

    assert limited.status_code == 429
    assert limited.json()["code"] == "RATE_LIMITED"
