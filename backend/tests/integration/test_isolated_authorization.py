"""第四阶段的隔离权限与资源边界集成测试。"""

from __future__ import annotations

import json
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database import Base, get_db
from app.main import app
from app.models import ApiEndpoint, ApiKey, ExternalResource, GatewayRequest, PublishedRoute, RemoteFile, Role, RoleName, Tool, ToolUpstream, User, UserRole
from app.services import gateway_proxy
from app.services.login_limit import reset_login_limit_state
from app.services.remote_files import ensure_storage_endpoint
from app.services.security import hash_api_key, hash_password


SYNTHETIC_PASSWORD = "SyntheticPass2026!"


class FakeUpstreamClient:
    """代替 tomoDD 和文件上游，确保测试不会创建网络连接。"""

    sent_requests: list[httpx.Request] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.closed = False

    def build_request(self, method: str, url: str, **kwargs: Any) -> httpx.Request:
        return httpx.Request(method, url, **kwargs)

    async def send(self, request: httpx.Request, stream: bool = False) -> httpx.Response:
        self.sent_requests.append(request)
        return httpx.Response(200, json={"source": "synthetic-upstream", "path": request.url.path})

    async def aclose(self) -> None:
        self.closed = True


@dataclass(frozen=True)
class SyntheticActors:
    """合成租户标签仅供测试分组，生产模型并不存在 tenant 字段。"""

    alpha_user_a: User
    alpha_user_b: User
    beta_user_a: User
    beta_user_b: User
    admin: User
    alpha_key: str
    alpha_peer_key: str
    beta_key: str
    admin_key: str
    alpha_limited_key: str
    tool: Tool
    owner_route: PublishedRoute
    shared_route: PublishedRoute
    admin_route: PublishedRoute
    alpha_resource: ExternalResource
    beta_resource: ExternalResource
    alpha_task: ExternalResource
    alpha_file: RemoteFile
    alpha_call: GatewayRequest
    beta_call: GatewayRequest


@pytest.fixture
def isolated_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[TestClient, SyntheticActors], None, None]:
    """创建文件型临时 SQLite、假 SMTP 配置和假的本地上游。"""

    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "app_secret_key", "synthetic-phase4-session-signing-key-000000000")
    monkeypatch.setattr(settings, "app_encryption_key", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
    monkeypatch.setattr(settings, "api_key_pepper", "synthetic-phase4-api-key-pepper-000000000")
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'phase4-authorization.db'}")
    monkeypatch.setattr(settings, "smtp_host", "smtp.test.invalid")
    monkeypatch.setattr(settings, "smtp_from", "noreply@example.test")
    monkeypatch.setattr(settings, "email_test_mode", True)
    monkeypatch.setattr(settings, "tomodd_base_url", "http://127.0.0.1:19091")
    monkeypatch.setattr(settings, "allowed_upstream_hosts", "127.0.0.1,localhost")
    monkeypatch.setattr(gateway_proxy.httpx, "AsyncClient", FakeUpstreamClient)
    FakeUpstreamClient.sent_requests = []
    reset_login_limit_state()

    engine = create_engine(f"sqlite:///{tmp_path / 'phase4-authorization.db'}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    with factory() as session:
        actors = _seed_synthetic_data(session)

    def override_database() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_database
    client = TestClient(app, base_url="https://testserver")
    try:
        yield client, actors
    finally:
        client.close()
        app.dependency_overrides.clear()
        reset_login_limit_state()
        engine.dispose()


def _new_user(username: str) -> User:
    return User(
        username=username,
        email=f"{username}@example.test",
        email_verified_at=datetime.now(timezone.utc),
        password_hash=hash_password(SYNTHETIC_PASSWORD),
        must_change_password=False,
    )


def _route(tool: Tool, *, path: str, access_mode: str, policy: dict[str, object]) -> PublishedRoute:
    endpoint = ApiEndpoint(
        tool_id=tool.id,
        method="GET",
        upstream_path=path,
        operation_id=f"synthetic_{access_mode}_{path.rsplit('/', 1)[-1]}",
        status="published",
    )
    route = PublishedRoute(
        tool_id=tool.id,
        endpoint_id=endpoint.id,
        method="GET",
        gateway_path=path,
        upstream_path=path,
        access_mode=access_mode,
        resource_policy_json=json.dumps(policy, ensure_ascii=False),
    )
    return endpoint, route


def _seed_synthetic_data(session: Session) -> SyntheticActors:
    user_role = Role(name=RoleName.USER.value, description="合成普通用户")
    admin_role = Role(name=RoleName.ADMIN.value, description="合成管理员")
    alpha_user_a = _new_user("tenant-alpha-user-a")
    alpha_user_b = _new_user("tenant-alpha-user-b")
    beta_user_a = _new_user("tenant-beta-user-a")
    beta_user_b = _new_user("tenant-beta-user-b")
    admin = _new_user("synthetic-admin")
    tool = Tool(slug="synthetic-tool", name="合成 tomoDD 工具", task_adapter_json="{}")
    session.add_all([user_role, admin_role, alpha_user_a, alpha_user_b, beta_user_a, beta_user_b, admin, tool])
    session.flush()
    session.add_all(
        [
            UserRole(user_id=alpha_user_a.id, role_id=user_role.id),
            UserRole(user_id=alpha_user_b.id, role_id=user_role.id),
            UserRole(user_id=beta_user_a.id, role_id=user_role.id),
            UserRole(user_id=beta_user_b.id, role_id=user_role.id),
            UserRole(user_id=admin.id, role_id=admin_role.id),
        ]
    )
    upstream = ToolUpstream(tool_id=tool.id, base_url="http://127.0.0.1:19091", token_ciphertext="")
    session.add(upstream)
    session.flush()
    storage_endpoint = ensure_storage_endpoint(session, tool, upstream)

    owner_endpoint, owner_route = _route(
        tool,
        path="/resources/{resource_id}",
        access_mode="owner",
        policy={
            "version": 1,
            "pre_checks": [{"action": "require_owner", "resource_kind": "dataset", "source": "path", "selector": "resource_id"}],
            "post_actions": [],
            "list_filter": None,
        },
    )
    shared_endpoint, shared_route = _route(tool, path="/shared/{resource_id}", access_mode="shared", policy={})
    admin_endpoint, admin_route = _route(tool, path="/admin/{resource_id}", access_mode="admin_only", policy={})
    session.add_all([owner_endpoint, shared_endpoint, admin_endpoint])
    session.flush()
    owner_route.endpoint_id = owner_endpoint.id
    shared_route.endpoint_id = shared_endpoint.id
    admin_route.endpoint_id = admin_endpoint.id
    session.add_all([owner_route, shared_route, admin_route])
    session.flush()

    alpha_resource = ExternalResource(tool_id=tool.id, owner_user_id=alpha_user_a.id, kind="dataset", upstream_id="alpha-dataset")
    beta_resource = ExternalResource(tool_id=tool.id, owner_user_id=beta_user_a.id, kind="dataset", upstream_id="beta-dataset")
    alpha_task = ExternalResource(
        tool_id=tool.id,
        owner_user_id=alpha_user_a.id,
        kind="job",
        upstream_id="alpha-job",
        storage_endpoint_revision_id=storage_endpoint.id,
    )
    alpha_file = RemoteFile(
        identity_key="phase4-alpha-file",
        tool_id=tool.id,
        owner_user_id=alpha_user_a.id,
        storage_endpoint_revision_id=storage_endpoint.id,
        upstream_file_id="alpha-file",
        file_name="alpha-result.txt",
        visibility="user",
        status="ready",
        size_bytes=16,
    )
    alpha_key = "agw_synthetic_alpha_key_for_phase4"
    alpha_peer_key = "agw_synthetic_alpha_peer_key"
    beta_key = "agw_synthetic_beta_key_for_phase4"
    admin_key = "agw_synthetic_admin_key_for_phase4"
    alpha_limited_key = "agw_synthetic_alpha_limited_key"
    session.add_all(
        [
            alpha_resource,
            beta_resource,
            alpha_task,
            alpha_file,
            ApiKey(user_id=alpha_user_a.id, label="Alpha", prefix=alpha_key[:16], secret_hash=hash_api_key(alpha_key), scopes="tool:synthetic-tool:*"),
            ApiKey(user_id=alpha_user_b.id, label="Alpha peer", prefix=alpha_peer_key[:16], secret_hash=hash_api_key(alpha_peer_key), scopes="tool:synthetic-tool:*"),
            ApiKey(user_id=beta_user_a.id, label="Beta", prefix=beta_key[:16], secret_hash=hash_api_key(beta_key), scopes="tool:synthetic-tool:*"),
            ApiKey(user_id=admin.id, label="Admin", prefix=admin_key[:16], secret_hash=hash_api_key(admin_key), scopes="tool:synthetic-tool:*"),
            ApiKey(user_id=alpha_user_a.id, label="Alpha limited", prefix=alpha_limited_key[:16], secret_hash=hash_api_key(alpha_limited_key), scopes="tool:other-tool:*"),
        ]
    )
    session.flush()
    alpha_call = GatewayRequest(id="11111111-1111-4111-8111-111111111111", user_id=alpha_user_a.id, tool_id=tool.id, tool_slug=tool.slug, method="GET", path="/resources/alpha-dataset", status_code=200)
    beta_call = GatewayRequest(id="22222222-2222-4222-8222-222222222222", user_id=beta_user_a.id, tool_id=tool.id, tool_slug=tool.slug, method="GET", path="/resources/beta-dataset", status_code=200)
    session.add_all([alpha_call, beta_call])
    session.commit()
    return SyntheticActors(
        alpha_user_a=alpha_user_a,
        alpha_user_b=alpha_user_b,
        beta_user_a=beta_user_a,
        beta_user_b=beta_user_b,
        admin=admin,
        alpha_key=alpha_key,
        alpha_peer_key=alpha_peer_key,
        beta_key=beta_key,
        admin_key=admin_key,
        alpha_limited_key=alpha_limited_key,
        tool=tool,
        owner_route=owner_route,
        shared_route=shared_route,
        admin_route=admin_route,
        alpha_resource=alpha_resource,
        beta_resource=beta_resource,
        alpha_task=alpha_task,
        alpha_file=alpha_file,
        alpha_call=alpha_call,
        beta_call=beta_call,
    )


def _login(client: TestClient, username: str) -> str:
    client.cookies.clear()
    response = client.post("/api/auth/login", json={"username": username, "password": SYNTHETIC_PASSWORD})
    assert response.status_code == 200, response.text
    csrf = response.cookies.get("agw_csrf")
    assert csrf
    return csrf


def test_protected_routes_reject_anonymous_requests(isolated_client: tuple[TestClient, SyntheticActors]) -> None:
    client, actors = isolated_client
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me/calls").status_code == 401
    assert client.get(f"/api/me/files/{actors.alpha_file.id}").status_code == 401
    assert client.get("/api/admin/users").status_code == 401


def test_owner_policy_allows_owner_and_hides_other_users(isolated_client: tuple[TestClient, SyntheticActors]) -> None:
    client, actors = isolated_client
    own = client.get("/gateway/synthetic-tool/resources/alpha-dataset", headers={"X-API-Key": actors.alpha_key})
    same_synthetic_tenant_other_user = client.get("/gateway/synthetic-tool/resources/alpha-dataset", headers={"X-API-Key": actors.alpha_peer_key})
    other_synthetic_tenant = client.get("/gateway/synthetic-tool/resources/beta-dataset", headers={"X-API-Key": actors.alpha_key})
    assert own.status_code == 200
    assert own.json()["source"] == "synthetic-upstream"
    assert same_synthetic_tenant_other_user.status_code == 404
    assert other_synthetic_tenant.status_code == 404
    assert same_synthetic_tenant_other_user.json()["code"] == "NOT_FOUND"
    assert "alpha-dataset" not in same_synthetic_tenant_other_user.text


def test_shared_and_admin_only_routes_follow_the_current_code_contract(isolated_client: tuple[TestClient, SyntheticActors]) -> None:
    client, actors = isolated_client
    shared = client.get("/gateway/synthetic-tool/shared/beta-dataset", headers={"X-API-Key": actors.alpha_key})
    normal_admin_only = client.get("/gateway/synthetic-tool/admin/control", headers={"X-API-Key": actors.alpha_key})
    admin_admin_only = client.get("/gateway/synthetic-tool/admin/control", headers={"X-API-Key": actors.admin_key})
    admin_owner = client.get("/gateway/synthetic-tool/resources/alpha-dataset", headers={"X-API-Key": actors.admin_key})
    assert shared.status_code == 200
    assert normal_admin_only.status_code == 403
    assert admin_admin_only.status_code == 200
    assert admin_owner.status_code == 404


def test_user_lists_details_exports_tasks_and_files_do_not_leak_other_owner_data(isolated_client: tuple[TestClient, SyntheticActors]) -> None:
    client, actors = isolated_client
    _login(client, actors.alpha_user_a.username)
    calls = client.get("/api/me/calls?page=1&page_size=10")
    export = client.get("/api/me/calls/export.csv")
    own_file = client.get(f"/api/me/files/{actors.alpha_file.id}")
    own_task = client.get(f"/api/me/tasks/{actors.alpha_task.id}")
    assert calls.status_code == 200
    assert {item["request_id"] for item in calls.json()["items"]} == {actors.alpha_call.id}
    assert actors.alpha_call.id in export.text
    assert actors.beta_call.id not in export.text
    assert own_file.status_code == 200
    assert own_task.status_code == 200

    _login(client, actors.alpha_user_b.username)
    assert client.get(f"/api/me/calls/{actors.alpha_call.id}").status_code == 404
    assert client.get(f"/api/me/files/{actors.alpha_file.id}").status_code == 404
    assert client.get(f"/api/me/tasks/{actors.alpha_task.id}").status_code == 404


def test_api_key_scope_and_admin_management_are_server_enforced(isolated_client: tuple[TestClient, SyntheticActors]) -> None:
    client, actors = isolated_client
    denied_scope = client.get("/gateway/synthetic-tool/resources/alpha-dataset", headers={"X-API-Key": actors.alpha_limited_key})
    assert denied_scope.status_code == 403
    assert denied_scope.json()["code"] == "API_KEY_SCOPE_DENIED"

    _login(client, actors.alpha_user_a.username)
    assert client.get("/api/admin/users").status_code == 403
    _login(client, actors.admin.username)
    admin_users = client.get("/api/admin/users")
    assert admin_users.status_code == 200
    assert {item["id"] for item in admin_users.json()} >= {actors.alpha_user_a.id, actors.beta_user_a.id}


def test_logout_and_password_change_invalidate_old_sessions(isolated_client: tuple[TestClient, SyntheticActors]) -> None:
    client, actors = isolated_client
    csrf = _login(client, actors.alpha_user_a.username)
    old_session = client.cookies.get(get_settings().session_cookie_name)
    assert old_session
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    client.cookies.set(get_settings().session_cookie_name, old_session)
    assert client.get("/api/me").status_code == 401

    csrf = _login(client, actors.alpha_user_a.username)
    pre_change_session = client.cookies.get(get_settings().session_cookie_name)
    changed = client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": SYNTHETIC_PASSWORD, "new_password": "UpdatedSyntheticPass2026!"},
    )
    assert changed.status_code == 200
    client.cookies.clear()
    client.cookies.set(get_settings().session_cookie_name, pre_change_session)
    assert client.get("/api/me").status_code == 401
