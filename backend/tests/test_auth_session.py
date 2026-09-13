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
from app.models import User
from app.services.security import hash_password


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory() as session:
        session.add(User(username="session-user", password_hash=hash_password("SessionPass2026!"), must_change_password=False))
        session.commit()

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
        engine.dispose()


def test_logout_invalidates_the_old_server_session(client: TestClient) -> None:
    settings = get_settings()
    login = client.post("/api/auth/login", json={"username": "session-user", "password": "SessionPass2026!"})
    assert login.status_code == 200
    old_session = login.cookies.get(settings.session_cookie_name)
    csrf = login.cookies.get("agw_csrf")
    assert old_session and csrf
    assert client.get("/api/me").status_code == 200

    logout = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 200
    client.cookies.set(settings.session_cookie_name, old_session)
    expired = client.get("/api/me")
    assert expired.status_code == 401
    assert expired.json()["code"] == "UNAUTHORIZED"
