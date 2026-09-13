from __future__ import annotations

import json
from collections.abc import Generator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import AdminAuditEvent, ApprovalStatus, AuthActionToken, EmailOutbox, GatewayRequest, PlatformSettings, Role, RoleName, User, UserRole
from app.config import get_settings
from app.services import gateway_proxy
from app.services.login_limit import reset_login_limit_state
from app.services.security import decrypt_secret, hash_password
from app.services.email_delivery import deliver_due_email
from app.services.user_lifecycle import add_notification, consume_action_token, get_valid_action_token, issue_action_token


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    reset_login_limit_state()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory() as session:
        user_role = Role(name=RoleName.USER.value, description="普通用户")
        admin_role = Role(name=RoleName.ADMIN.value, description="管理员")
        admin = User(
            username="admin",
            password_hash=hash_password("AdminPass2026!"),
            is_active=True,
            approval_status=ApprovalStatus.APPROVED.value,
            must_change_password=False,
        )
        session.add_all([user_role, admin_role, admin])
        session.flush()
        session.add(UserRole(user_id=admin.id, role_id=admin_role.id))
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
        reset_login_limit_state()
        engine.dispose()


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    csrf = response.cookies.get("agw_csrf")
    assert csrf
    return csrf


def _accept_diff(client: TestClient, csrf: str, diff: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"decision": "accept", "reason": "测试确认接口访问策略", "use_suggestion": True}
    suggestion = diff["access_policy_suggestion"]
    if not suggestion["complete"]:
        payload["access_policy"] = {
            "access_mode": "shared",
            "rules": suggestion["rules"],
            "shared_confirm": True,
        }
    response = client.patch(
        f"/api/admin/diffs/{diff['id']}/decision",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _accept_all_diffs(client: TestClient, csrf: str, parsed: dict[str, Any]) -> None:
    for diff in parsed["diffs"]:
        _accept_diff(client, csrf, diff)
    confirmed = client.post(
        f"/api/admin/import-batches/{parsed['batch']['id']}/confirm",
        json={"confirm": True, "reason": "测试确认全部接口治理决策"},
        headers={"X-CSRF-Token": csrf},
    )
    assert confirmed.status_code == 200, confirmed.text


def test_public_registration_waits_for_admin_approval(client: TestClient) -> None:
    registered = client.post(
        "/api/auth/register",
        json={
            "username": "Alice",
            "email": "Alice@example.com",
            "password": "StrongPass2026!",
            "display_name": "Alice",
            "registration_note": "需要接入科研 API",
        },
    )
    assert registered.status_code == 201
    assert registered.json()["username"] == "Alice"
    assert registered.json()["approval_status"] == "pending"
    assert registered.json()["is_active"] is False

    pending_login = client.post("/api/auth/login", json={"username": "alice", "password": "StrongPass2026!"})
    assert pending_login.status_code == 403
    assert pending_login.json()["code"] == "PENDING_APPROVAL"

    csrf = _login(client, "admin", "AdminPass2026!")
    pending = client.get("/api/admin/users/pending")
    assert pending.status_code == 200
    assert [item["username"] for item in pending.json()] == ["Alice"]

    approved = client.post("/api/admin/users/" + registered.json()["id"] + "/approve", json={"reason": "资料完整"}, headers={"X-CSRF-Token": csrf})
    assert approved.status_code == 200
    assert approved.json()["approval_status"] == "approved"
    assert approved.json()["is_active"] is True

    user_csrf = _login(client, "ALICE", "StrongPass2026!")
    created_key = client.post("/api/me/api-keys", json={"label": "全局 Key"}, headers={"X-CSRF-Token": user_csrf})
    assert created_key.status_code == 201
    assert created_key.json()["secret"].startswith("agw_")
    assert created_key.json()["scopes"] == ["*"]


def test_rejected_registration_cannot_login(client: TestClient) -> None:
    registered = client.post(
        "/api/auth/register",
        json={"username": "bob", "email": "bob@example.com", "password": "StrongPass2026!", "registration_note": "测试拒绝"},
    )
    assert registered.status_code == 201
    csrf = _login(client, "admin", "AdminPass2026!")
    rejected = client.post(
        "/api/admin/users/" + registered.json()["id"] + "/reject",
        json={"reason": "用途说明不足"},
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected.status_code == 200
    assert rejected.json()["approval_status"] == "rejected"

    login = client.post("/api/auth/login", json={"username": "bob", "password": "StrongPass2026!"})
    assert login.status_code == 403
    assert login.json()["code"] == "REGISTRATION_REJECTED"


def test_admin_user_overview_and_disable_reason_form_a_closed_loop(client: TestClient) -> None:
    registered = client.post(
        "/api/auth/register",
        json={
            "username": "overviewuser",
            "email": "overview@example.com",
            "password": "StrongPass2026!",
            "display_name": "总览测试用户",
            "registration_note": "验证用户管理总览",
        },
    )
    assert registered.status_code == 201
    user_id = registered.json()["id"]
    admin_csrf = _login(client, "admin", "AdminPass2026!")

    pending = client.get("/api/admin/users/overview?state=pending&search=overview")
    assert pending.status_code == 200
    assert pending.json()["total"] == 1
    assert pending.json()["items"][0]["username"] == "overviewuser"
    assert pending.json()["stats"]["pending"] == 1

    approved = client.post(
        f"/api/admin/users/{user_id}/approve",
        json={"reason": "资料完整"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert approved.status_code == 200
    duplicate_approval = client.post(
        f"/api/admin/users/{user_id}/approve",
        json={"reason": "重复操作"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert duplicate_approval.status_code == 409

    user_csrf = _login(client, "overviewuser", "StrongPass2026!")
    key = client.post(
        "/api/me/api-keys",
        json={"label": "总览测试 Key"},
        headers={"X-CSRF-Token": user_csrf},
    )
    assert key.status_code == 201

    admin_csrf = _login(client, "admin", "AdminPass2026!")
    active = client.get("/api/admin/users/overview?state=active&search=overview")
    assert active.status_code == 200
    assert active.json()["items"][0]["api_key_count"] == 1
    assert active.json()["items"][0]["active_api_key_count"] == 1

    disabled = client.post(
        f"/api/admin/users/{user_id}/disable",
        json={"reason": "项目权限已到期"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert disabled.status_code == 200
    assert disabled.json()["disable_reason"] == "项目权限已到期"
    assert disabled.json()["disabled_at"] is not None

    disabled_login = client.post(
        "/api/auth/login",
        json={"username": "overviewuser", "password": "StrongPass2026!"},
    )
    assert disabled_login.status_code == 403
    assert disabled_login.json()["code"] == "ACCOUNT_DISABLED"
    assert disabled_login.json()["details"]["reason"] == "项目权限已到期"

    enabled = client.post(
        f"/api/admin/users/{user_id}/enable",
        json={"reason": "权限已续期"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert enabled.status_code == 200
    assert enabled.json()["disable_reason"] == ""
    assert enabled.json()["disabled_at"] is None


def test_registration_rejects_duplicate_email(client: TestClient) -> None:
    first = client.post(
        "/api/auth/register",
        json={"username": "first", "email": "same@example.com", "password": "StrongPass2026!"},
    )
    assert first.status_code == 201
    duplicate = client.post(
        "/api/auth/register",
        json={"username": "second", "email": "SAME@example.com", "password": "StrongPass2026!"},
    )
    assert duplicate.status_code == 409


def test_registration_accepts_unicode_and_single_character_username(client: TestClient) -> None:
    created = client.post(
        "/api/auth/register",
        json={
            "username": " 刘 ",
            "email": "unicode@example.com",
            "password": "StrongPass2026!",
            "display_name": "应大视觉实验室",
            "registration_note": "测试中文用户名注册",
        },
    )
    assert created.status_code == 201
    assert created.json()["username"] == "刘"

    pending_login = client.post("/api/auth/login", json={"username": "刘", "password": "StrongPass2026!"})
    assert pending_login.status_code == 403
    assert pending_login.json()["code"] == "PENDING_APPROVAL"

    duplicate = client.post("/api/auth/register", json={"username": "刘", "password": "StrongPass2026!"})
    assert duplicate.status_code == 409

    invalid = client.post("/api/auth/register", json={"username": "坏\n名字", "password": "StrongPass2026!"})
    assert invalid.status_code == 422


def test_api_key_audit_events_are_redacted(client: TestClient) -> None:
    registered = client.post("/api/auth/register", json={"username": "keyuser", "password": "StrongPass2026!"})
    csrf = _login(client, "admin", "AdminPass2026!")
    client.post("/api/admin/users/" + registered.json()["id"] + "/approve", json={"reason": "允许测试"}, headers={"X-CSRF-Token": csrf})
    user_csrf = _login(client, "keyuser", "StrongPass2026!")
    created = client.post("/api/me/api-keys", json={"label": "默认全局 Key"}, headers={"X-CSRF-Token": user_csrf})
    key_id = created.json()["id"]
    assert created.status_code == 201
    disabled = client.patch(f"/api/me/api-keys/{key_id}/disable", headers={"X-CSRF-Token": user_csrf})
    assert disabled.status_code == 200

    csrf = _login(client, "admin", "AdminPass2026!")
    audits = client.get("/api/admin/audit?action=api_key_created", headers={"X-CSRF-Token": csrf})
    assert audits.status_code == 200
    assert audits.json()[0]["action"] == "api_key_created"
    assert "secret" not in str(audits.json()[0]["detail"]).lower()
    found = client.get(f"/api/admin/api-keys?prefix={created.json()['prefix'][:8]}", headers={"X-CSRF-Token": csrf})
    assert found.status_code == 200
    assert found.json()[0]["prefix"] == created.json()["prefix"]
    assert found.json()[0]["status"] == "disabled"
    assert found.json()[0]["disabled_at"] is not None
    assert found.json()[0]["disable_reason"] == "用户主动禁用"
    assert found.json()[0]["recent_failure_count"] == 0


def test_admin_onboarding_info_exposes_safe_gateway_contract(client: TestClient) -> None:
    csrf = _login(client, "admin", "AdminPass2026!")
    response = client.get("/api/admin/platform/onboarding", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    payload = response.json()
    assert payload["gateway_pattern"] == "/gateway/{tool_slug}{gateway_path}"
    assert payload["supported_methods"] == ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]
    assert payload["access_modes"] == ["authenticated", "owner", "shared", "admin_only"]
    assert "127.0.0.1" in payload["allowed_upstream_hosts"]
    assert "token" not in str(payload).lower()


def test_user_registration_writes_audit_event(client: TestClient) -> None:
    response = client.post("/api/auth/register", json={"username": "audituser", "password": "StrongPass2026!"})
    assert response.status_code == 201
    _login(client, "admin", "AdminPass2026!")
    audits = client.get("/api/admin/audit?action=user_registered")
    assert audits.status_code == 200
    assert audits.json()
    assert "StrongPass" not in str(audits.json()[0]["detail"])


def test_admin_audit_supports_paginated_operational_filters(client: TestClient) -> None:
    registered = client.post("/api/auth/register", json={"username": "auditfilteruser", "password": "StrongPass2026!"})
    assert registered.status_code == 201
    csrf = _login(client, "admin", "AdminPass2026!")
    approved = client.post(
        f"/api/admin/users/{registered.json()['id']}/approve",
        json={"reason": "验证审计筛选"},
        headers={"X-CSRF-Token": csrf},
    )
    assert approved.status_code == 200

    response = client.get(
        f"/api/admin/audit?page=1&page_size=10&action=user_approved&target_type=user&target_id={registered.json()['id']}&admin_username=adm"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["page"] == 1
    assert payload["has_more"] is False
    assert payload["items"][0]["admin_username"] == "admin"
    assert payload["items"][0]["detail"]["reason"] == "验证审计筛选"
    assert "user_approved" in payload["available_actions"]
    assert "user" in payload["available_target_types"]

    invalid_window = client.get("/api/admin/audit?page=1&start=2026-08-10T12:00&end=2026-08-10T10:00")
    assert invalid_window.status_code == 422


def test_generic_resource_endpoint_requires_confirmed_owner_policy_before_publish(client: TestClient) -> None:
    csrf = _login(client, "admin", "AdminPass2026!")
    created = client.post(
        "/api/admin/tools",
        json={
            "slug": "generic-jobs",
            "name": "Generic Jobs",
            "description": "资源型通用工具",
            "base_url": "http://127.0.0.1:18000",
            "upstream_token": "",
            "status": "active",
            "auth_type": "none",
            "health_path": "/health",
            "environment_mode": "single",
            "environments": [{"name": "prod", "instance_name": "default", "base_url": "http://127.0.0.1:18000", "enabled": True}],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    tool_id = created.json()["id"]

    parsed = client.post(
        f"/api/admin/tools/{tool_id}/openapi/parse",
        json={
            "source_type": "text",
            "document_text": """
openapi: 3.1.0
info:
  title: Generic Jobs
paths:
  /api/v1/jobs/{job_id}:
    get:
      operationId: getJob
      summary: 查询任务
""",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert parsed.status_code == 200
    endpoints = client.get(f"/api/admin/tools/{tool_id}/endpoints")
    assert endpoints.status_code == 200
    endpoint = endpoints.json()[0]
    assert endpoint["status"] == "candidate"
    assert endpoint["access_policy"]["access_mode"] == "owner"
    assert endpoint["access_policy"]["complete"] is True
    assert endpoint["access_policy"]["confirmed"] is False

    published = client.patch(
        f"/api/admin/endpoints/{endpoint['id']}/publish",
        json={"is_enabled": True, "confirm": True, "reason": "尝试发布资源型接口"},
        headers={"X-CSRF-Token": csrf},
    )
    assert published.status_code == 422

    _accept_diff(client, csrf, parsed.json()["diffs"][0])
    published = client.patch(
        f"/api/admin/endpoints/{endpoint['id']}/publish",
        json={"is_enabled": True, "confirm": True, "reason": "确认创建者策略后发布"},
        headers={"X-CSRF-Token": csrf},
    )
    assert published.status_code == 200
    assert published.json()["route_policy"]["access_mode"] == "owner"


def test_endpoint_catalog_supports_pagination_filters_and_direct_detail(client: TestClient) -> None:
    csrf = _login(client, "admin", "AdminPass2026!")
    created = client.post(
        "/api/admin/tools",
        json={
            "slug": "catalog-scale",
            "name": "Catalog Scale",
            "description": "分页接口目录测试",
            "base_url": "http://127.0.0.1:18000",
            "upstream_token": "",
            "status": "active",
            "auth_type": "none",
            "health_path": "/health",
            "environment_mode": "single",
            "environments": [],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    tool_id = created.json()["id"]
    paths: dict[str, Any] = {}
    for index in range(12):
        method = "get" if index % 2 == 0 else "post"
        paths[f"/api/v1/items/{index}"] = {
            method: {
                "operationId": f"operation{index}",
                "summary": f"Operation {index}",
                "tags": ["读取" if method == "get" else "写入"],
                "responses": {"200": {"description": "ok"}},
            }
        }
    parsed = client.post(
        f"/api/admin/tools/{tool_id}/openapi/parse",
        json={
            "source_type": "text",
            "document_text": json.dumps(
                {"openapi": "3.1.0", "info": {"title": "Catalog Scale"}, "paths": paths},
                ensure_ascii=False,
            ),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert parsed.status_code == 200

    first_page = client.get(f"/api/admin/tools/{tool_id}/endpoint-catalog?page=1&page_size=10")
    assert first_page.status_code == 200
    assert first_page.json()["total"] == 12
    assert first_page.json()["page_count"] == 2
    assert len(first_page.json()["items"]) == 10
    assert first_page.json()["methods"] == ["GET", "POST"]
    assert first_page.json()["groups"] == ["写入", "读取"]

    second_page = client.get(f"/api/admin/tools/{tool_id}/endpoint-catalog?page=2&page_size=10")
    assert second_page.status_code == 200
    assert len(second_page.json()["items"]) == 2

    filtered = client.get(
        f"/api/admin/tools/{tool_id}/endpoint-catalog?search=operation%2011&method=POST&group_name=%E5%86%99%E5%85%A5&page_size=10"
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    endpoint = filtered.json()["items"][0]
    assert endpoint["operation_id"] == "operation11"

    detail = client.get(f"/api/admin/endpoints/{endpoint['id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == endpoint["id"]
    assert client.get("/api/admin/endpoints/missing-endpoint").status_code == 404


def test_tool_onboarding_versions_and_endpoint_policy_expose_resource_governance(client: TestClient) -> None:
    csrf = _login(client, "admin", "AdminPass2026!")
    platform = client.get("/api/admin/platform/onboarding", headers={"X-CSRF-Token": csrf})
    assert platform.status_code == 200
    assert platform.json()["access_modes"] == ["authenticated", "owner", "shared", "admin_only"]
    assert "adapters" not in platform.json()

    created = client.post(
        "/api/admin/tools",
        json={
            "slug": "governed-generic",
            "name": "Governed Generic",
            "description": "resource governance demo",
            "base_url": "http://127.0.0.1:18000",
            "upstream_token": "",
            "status": "active",
            "auth_type": "none",
            "health_path": "/health",
            "environment_mode": "single",
            "environments": [{"name": "prod", "instance_name": "default", "base_url": "http://127.0.0.1:18000", "enabled": True, "priority": 100}],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    tool_id = created.json()["id"]

    parsed = client.post(
        f"/api/admin/tools/{tool_id}/openapi/parse",
        json={
            "source_type": "text",
            "document_text": """
openapi: 3.1.0
info:
  title: Governed Generic
paths:
  /health:
    get:
      operationId: health
      summary: health
  /api/v1/jobs/{job_id}:
    get:
      operationId: getJob
      summary: 查询任务
""",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert parsed.status_code == 200
    batch_id = parsed.json()["batch"]["id"]
    assert parsed.json()["batch"]["governance_summary"]["blocked_count"] == 0

    onboarding = client.get(f"/api/admin/tools/{tool_id}/onboarding", headers={"X-CSRF-Token": csrf})
    assert onboarding.status_code == 200
    assert onboarding.json()["pending_access_policy_count"] == 2
    assert "接口鉴权策略" in [item["label"] for item in onboarding.json()["steps"]]

    versions = client.get(f"/api/admin/tools/{tool_id}/versions", headers={"X-CSRF-Token": csrf})
    assert versions.status_code == 200
    assert versions.json()["network_isolation_mode"] == "firewall_allowlist"
    assert versions.json()["batches"][0]["id"] == batch_id
    assert versions.json()["batches"][0]["governance_summary"]["requires_decision"] is True

    endpoints = client.get(f"/api/admin/tools/{tool_id}/endpoints", headers={"X-CSRF-Token": csrf})
    assert endpoints.status_code == 200
    resource_endpoint = next(item for item in endpoints.json() if item["upstream_path"] == "/api/v1/jobs/{job_id}")
    assert resource_endpoint["access_policy"]["access_mode"] == "owner"
    assert resource_endpoint["access_policy"]["complete"] is True
    assert resource_endpoint["access_policy"]["confirmed"] is False

    excluded = client.post(
        f"/api/admin/endpoints/{resource_endpoint['id']}/exclude",
        json={"reason": "资源归属适配器未实现，首版排除"},
        headers={"X-CSRF-Token": csrf},
    )
    assert excluded.status_code == 200
    assert excluded.json()["status"] == "excluded"
    audits = client.get("/api/admin/audit?action=endpoint_excluded", headers={"X-CSRF-Token": csrf})
    assert audits.status_code == 200
    assert audits.json()[0]["target_id"] == resource_endpoint["id"]


def test_import_batch_confirmation_requires_all_diff_decisions(client: TestClient) -> None:
    csrf = _login(client, "admin", "AdminPass2026!")
    created = client.post(
        "/api/admin/tools",
        json={
            "slug": "confirm-gate",
            "name": "Confirm Gate",
            "description": "import confirmation gate",
            "base_url": "http://127.0.0.1:18000",
            "upstream_token": "",
            "status": "active",
            "auth_type": "none",
            "health_path": "/health",
            "environment_mode": "single",
            "environments": [{"name": "prod", "instance_name": "default", "base_url": "http://127.0.0.1:18000", "enabled": True, "priority": 100}],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    tool_id = created.json()["id"]
    parsed = client.post(
        f"/api/admin/tools/{tool_id}/openapi/parse",
        json={
            "source_type": "text",
            "document_text": """
openapi: 3.1.0
info:
  title: Confirm Gate
paths:
  /health:
    get:
      operationId: health
      summary: health
  /api/v1/admin/status:
    get:
      operationId: adminStatus
      summary: admin status
""",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert parsed.status_code == 200
    batch_id = parsed.json()["batch"]["id"]

    rejected = client.post(f"/api/admin/import-batches/{batch_id}/confirm", headers={"X-CSRF-Token": csrf})
    assert rejected.status_code == 422

    diffs = client.get(f"/api/admin/import-batches/{batch_id}/diffs", headers={"X-CSRF-Token": csrf})
    assert diffs.status_code == 200
    blockers = [item for item in diffs.json() if item["risk_level"] == "blocker"]
    assert blockers
    for index, diff in enumerate(blockers):
        decided = client.patch(
            f"/api/admin/diffs/{diff['id']}/decision",
            json={"decision": "block"} if index == 0 else {"decision": "block", "reason": "管理员接口默认阻断"},
            headers={"X-CSRF-Token": csrf},
        )
        assert decided.status_code == 200
        assert decided.json()["decision"] == "block"
        if index == 0:
            assert decided.json()["detail"]["decision_reason"] == "管理员在接口治理列表中确认接口决策"

    still_pending = client.post(
        f"/api/admin/import-batches/{batch_id}/confirm",
        json={"confirm": True, "reason": "验证普通接口仍未完成决策"},
        headers={"X-CSRF-Token": csrf},
    )
    assert still_pending.status_code == 422

    safe_diffs = [item for item in diffs.json() if item["risk_level"] != "blocker"]
    assert safe_diffs
    for diff in safe_diffs:
        _accept_diff(client, csrf, diff)

    confirmed = client.post(
        f"/api/admin/import-batches/{batch_id}/confirm",
        json={"confirm": True, "reason": "确认阻断风险已经完成治理"},
        headers={"X-CSRF-Token": csrf},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "applied"
    audits = client.get("/api/admin/audit?action=openapi_import_confirmed", headers={"X-CSRF-Token": csrf})
    assert audits.status_code == 200
    assert audits.json()[0]["target_id"] == batch_id


def test_gateway_call_is_visible_in_admin_monitor(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAsyncClient:
        send_count = 0

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            assert kwargs["verify"] is True

        def build_request(self, method: str, url: str, **kwargs: Any) -> httpx.Request:
            return httpx.Request(method, url, **kwargs)

        async def send(self, request: httpx.Request, stream: bool = False) -> httpx.Response:
            assert request.url.path == "/health"
            FakeAsyncClient.send_count += 1
            status_code = 503 if FakeAsyncClient.send_count == 1 else 200
            return httpx.Response(status_code, json={"status": "ok"}, headers={"content-type": "application/json"})

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(gateway_proxy.httpx, "AsyncClient", FakeAsyncClient)
    admin_csrf = _login(client, "admin", "AdminPass2026!")
    tool = client.post(
        "/api/admin/tools",
        json={
            "slug": "demo-monitor",
            "name": "Demo Monitor",
            "description": "监控测试工具",
            "base_url": "http://127.0.0.1:18000",
            "upstream_token": "",
            "status": "active",
            "auth_type": "none",
            "health_path": "/health",
            "environment_mode": "single",
            "environments": [{"name": "prod", "instance_name": "default", "base_url": "http://127.0.0.1:18000", "enabled": True}],
            "retry_count": 1,
        },
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert tool.status_code == 201
    tool_id = tool.json()["id"]
    parsed = client.post(
        f"/api/admin/tools/{tool_id}/openapi/parse",
        json={
            "source_type": "text",
            "document_text": """
openapi: 3.1.0
info:
  title: Demo Monitor
paths:
  /health:
    get:
      operationId: health
      summary: 健康检查
""",
        },
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert parsed.status_code == 200
    _accept_all_diffs(client, admin_csrf, parsed.json())
    endpoint = client.get(f"/api/admin/tools/{tool_id}/endpoints").json()[0]
    published = client.patch(
        f"/api/admin/endpoints/{endpoint['id']}/publish",
        json={"is_enabled": True, "confirm": True, "reason": "发布监控测试接口"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert published.status_code == 200

    registered = client.post("/api/auth/register", json={"username": "monitoruser", "password": "StrongPass2026!"})
    assert registered.status_code == 201
    admin_csrf = _login(client, "admin", "AdminPass2026!")
    client.post(f"/api/admin/users/{registered.json()['id']}/approve", json={"reason": "监控测试"}, headers={"X-CSRF-Token": admin_csrf})
    user_csrf = _login(client, "monitoruser", "StrongPass2026!")
    key = client.post("/api/me/api-keys", json={"label": "监控测试 Key"}, headers={"X-CSRF-Token": user_csrf})
    assert key.status_code == 201

    gateway = client.get("/gateway/demo-monitor/health", headers={"X-API-Key": key.json()["secret"]})
    assert gateway.status_code == 200
    assert FakeAsyncClient.send_count == 2

    user_calls = client.get(
        f"/api/me/calls?page=1&page_size=10&request_id={gateway.headers['x-request-id']}&tool_slug=demo-monitor&status_code=200"
    )
    assert user_calls.status_code == 200
    assert user_calls.json()["total"] == 1
    assert user_calls.json()["items"][0]["request_id"] == gateway.headers["x-request-id"]
    assert user_calls.json()["items"][0]["tool_slug"] == "demo-monitor"
    user_call_detail = client.get(f"/api/me/calls/{gateway.headers['x-request-id']}")
    assert user_call_detail.status_code == 200
    assert user_call_detail.json()["failure_stage"] == "completed"
    assert user_call_detail.json()["upstream_path"] == ""
    assert user_call_detail.json()["client_ip_fingerprint"] == ""

    failed_login = client.post("/api/auth/login", json={"username": "monitoruser", "password": "WrongPass2026!"})
    assert failed_login.status_code == 401

    admin_csrf = _login(client, "admin", "AdminPass2026!")
    hidden_from_other_user = client.get(f"/api/me/calls/{gateway.headers['x-request-id']}")
    assert hidden_from_other_user.status_code == 404

    profile = client.get(f"/api/admin/users/{registered.json()['id']}")
    assert profile.status_code == 200
    assert profile.json()["username"] == "monitoruser"
    assert profile.json()["api_key_count"] >= 1
    assert profile.json()["active_api_key_count"] >= 1
    profile_summary = client.get(f"/api/admin/users/{registered.json()['id']}/summary?window_days=7")
    assert profile_summary.status_code == 200
    assert profile_summary.json()["user"]["api_key_count"] >= 1
    assert profile_summary.json()["user"]["active_api_key_count"] >= 1
    assert profile_summary.json()["metrics"]["total"] == 1
    assert profile_summary.json()["metrics"]["success"] == 1
    assert profile_summary.json()["top_tools"][0]["tool_slug"] == "demo-monitor"
    profile_calls = client.get(
        f"/api/admin/users/{registered.json()['id']}/calls?page=1&page_size=10&result=success&method=GET&api_key_id={key.json()['id']}"
    )
    assert profile_calls.status_code == 200
    assert profile_calls.json()["total"] == 1
    admin_call_detail = client.get(f"/api/admin/calls/{gateway.headers['x-request-id']}")
    assert admin_call_detail.status_code == 200
    assert admin_call_detail.json()["username"] == "monitoruser"
    assert admin_call_detail.json()["api_key_prefix"].startswith("agw_")
    assert admin_call_detail.json()["upstream_path"] == "/health"
    events = client.get(f"/api/admin/users/{registered.json()['id']}/events?page=1&page_size=20")
    assert events.status_code == 200
    event_actions = {item["action"] for item in events.json()["items"]}
    assert {"user_registered", "user_approved", "user_logged_in", "user_login_failed", "api_key_created"}.issubset(event_actions)

    summary = client.get("/api/admin/monitor/summary?tool_slug=demo-monitor")
    assert summary.status_code == 200
    assert summary.json()["total_requests"] == 1
    metrics = client.get("/api/admin/monitor/metrics?tool_slug=demo-monitor")
    assert metrics.status_code == 200
    assert metrics.json()["requests"]["total"] == 1
    assert metrics.json()["requests"]["success_rate"] == 1
    assert {"avg", "p50", "p95", "p99"}.issubset(metrics.json()["latency_ms"].keys())
    requests = client.get("/api/admin/monitor/requests?tool_slug=demo-monitor")
    assert requests.status_code == 200
    assert requests.json()[0]["username"] == "monitoruser"
    assert requests.json()[0]["api_key_prefix"].startswith("agw_")

    user_keys = client.get(f"/api/admin/users/{registered.json()['id']}/api-keys")
    assert user_keys.status_code == 200
    assert user_keys.json()[0]["prefix"].startswith("agw_")
    disabled = client.patch(
        f"/api/admin/api-keys/{user_keys.json()[0]['id']}/disable",
        json={"reason": "发现异常调用，管理员禁用"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    for _ in range(3):
        rejected_gateway = client.get("/gateway/demo-monitor/health", headers={"X-API-Key": key.json()["secret"]})
        assert rejected_gateway.status_code == 401
    alerts = client.get("/api/admin/monitor/alerts?tool_slug=demo-monitor", headers={"X-CSRF-Token": admin_csrf})
    assert alerts.status_code == 200
    assert any(item["type"] == "key_failure_rate" and item["api_key_prefix"].startswith("agw_") for item in alerts.json())
    metrics = client.get("/api/admin/monitor/metrics?tool_slug=demo-monitor", headers={"X-CSRF-Token": admin_csrf})
    assert metrics.status_code == 200
    assert metrics.json()["key_anomalies"]["count"] >= 1
    assert metrics.json()["alerts"]["count"] >= 1


def test_gateway_entry_failures_are_uniform_and_queryable_by_request_id(client: TestClient) -> None:
    response = client.get("/gateway/unknown-tool/health")
    assert response.status_code == 401
    payload = response.json()
    assert payload["code"] == "MISSING_API_KEY"
    assert payload["request_id"]

    csrf = _login(client, "admin", "AdminPass2026!")
    requests = client.get(f"/api/admin/monitor/requests?request_id={payload['request_id']}", headers={"X-CSRF-Token": csrf})
    assert requests.status_code == 200
    assert requests.json()[0]["request_id"] == payload["request_id"]
    assert requests.json()[0]["status_code"] == 401
    assert requests.json()[0]["error_code"] == "MISSING_API_KEY"
    assert requests.json()[0]["tool_slug"] == "unknown-tool"


def test_admin_tool_contract_uses_single_deployment_server(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeHealthClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeHealthClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str, **kwargs: Any) -> httpx.Response:
            assert url == "http://127.0.0.1:18000/health"
            return httpx.Response(200, json={"status": "ok"}, headers={"content-type": "application/json"})

    from app.routers import admin as admin_router

    monkeypatch.setattr(admin_router.httpx, "AsyncClient", FakeHealthClient)
    csrf = _login(client, "admin", "AdminPass2026!")
    tool = client.post(
        "/api/admin/tools",
        json={
            "slug": "instance-demo",
            "name": "Instance Demo",
            "description": "single deployment server contract",
            "base_url": "http://127.0.0.1:18000",
            "upstream_token": "",
            "status": "active",
            "auth_type": "none",
            "health_path": "/health",
            "environment_mode": "multi",
            "environments": [
                {"name": "prod", "instance_name": "default", "base_url": "http://127.0.0.1:18000", "enabled": True, "priority": 100},
                {"name": "staging", "instance_name": "backup", "base_url": "http://127.0.0.1:18000", "enabled": True, "priority": 50},
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert tool.status_code == 201
    tool_id = tool.json()["id"]
    assert tool.json()["environment_mode"] == "single"
    assert tool.json()["environments"] == []
    assert tool.json()["base_url_masked"] == "http://127.0.0.1:18000"

    removed_list = client.get(f"/api/admin/tools/{tool_id}/upstream-instances")
    assert removed_list.status_code == 404

    health = client.post(f"/api/admin/tools/{tool_id}/health/test", headers={"X-CSRF-Token": csrf})
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert "instances" not in health.json()

    async def unexpected_health_check(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("工具列表读取不应访问上游健康地址")

    monkeypatch.setattr(admin_router, "check_tool_health", unexpected_health_check)
    listed = client.get("/api/admin/tools")
    assert listed.status_code == 200
    listed_tool = next(item for item in listed.json() if item["id"] == tool_id)
    assert listed_tool["health"]["status"] == "ok"
    assert listed_tool["health"]["reachable"] is True

    dashboard = client.get("/api/admin/dashboard/tools")
    assert dashboard.status_code == 200
    dashboard_tool = next(item for item in dashboard.json() if item["id"] == tool_id)
    assert dashboard_tool["health"]["status"] == "ok"


def test_openapi_url_import_discovers_spec_from_swagger_ui_page(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDocsClient:
        requested_urls: list[str] = []

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeDocsClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str, **kwargs: Any) -> httpx.Response:
            self.requested_urls.append(url)
            if url.endswith("/health"):
                return httpx.Response(200, json={"status": "ok"}, headers={"content-type": "application/json"})
            if url.endswith("/docs"):
                return httpx.Response(
                    200,
                    text="""<!doctype html><html><script>SwaggerUIBundle({url: '/openapi.json'})</script></html>""",
                    headers={"content-type": "text/html; charset=utf-8"},
                )
            if url.endswith("/openapi.json"):
                return httpx.Response(
                    200,
                    json={
                        "openapi": "3.1.0",
                        "info": {"title": "Docs URL Demo", "version": "1.0.0"},
                        "paths": {
                            "/health": {
                                "get": {
                                    "operationId": "health",
                                    "summary": "Health",
                                    "responses": {"200": {"description": "ok"}},
                                }
                            }
                        },
                    },
                    headers={"content-type": "application/json"},
                )
            return httpx.Response(404, json={"detail": "not found"}, headers={"content-type": "application/json"})

    from app.routers import admin as admin_router

    monkeypatch.setattr(admin_router.httpx, "AsyncClient", FakeDocsClient)
    csrf = _login(client, "admin", "AdminPass2026!")
    tool = client.post(
        "/api/admin/tools",
        json={
            "slug": "docs-url-demo",
            "name": "Docs URL Demo",
            "description": "导入 Swagger UI 页面时自动发现规范地址",
            "base_url": "http://127.0.0.1:18000",
            "upstream_token": "",
            "status": "active",
            "auth_type": "none",
            "health_path": "/health",
            "environment_mode": "single",
            "environments": [{"name": "prod", "instance_name": "default", "base_url": "http://127.0.0.1:18000", "enabled": True, "priority": 100}],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert tool.status_code == 201
    tool_id = tool.json()["id"]

    parsed = client.post(
        f"/api/admin/tools/{tool_id}/openapi/parse",
        json={"source_type": "url", "source_url": " http://127.0.0.1:18000/docs "},
        headers={"X-CSRF-Token": csrf},
    )
    assert parsed.status_code == 200
    assert parsed.json()["batch"]["source_type"] == "url"
    assert parsed.json()["batch"]["source_url"] == "http://127.0.0.1/openapi.json"
    assert parsed.json()["diffs"][0]["gateway_path"] == "/health"
    assert "http://127.0.0.1:18000/docs" in FakeDocsClient.requested_urls
    assert "http://127.0.0.1:18000/openapi.json" in FakeDocsClient.requested_urls


def test_openapi_description_updates_generated_endpoint_description(client: TestClient) -> None:
    csrf = _login(client, "admin", "AdminPass2026!")
    tool = client.post(
        "/api/admin/tools",
        json={
            "slug": "description-demo",
            "name": "Description Demo",
            "description": "接口说明同步测试",
            "base_url": "http://127.0.0.1:18000",
            "upstream_token": "",
            "status": "active",
            "auth_type": "none",
            "health_path": "/health",
            "environment_mode": "single",
            "environments": [{"name": "prod", "instance_name": "default", "base_url": "http://127.0.0.1:18000", "enabled": True, "priority": 100}],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert tool.status_code == 201
    tool_id = tool.json()["id"]

    first = client.post(
        f"/api/admin/tools/{tool_id}/openapi/parse",
        json={
            "source_type": "text",
            "document_text": """
openapi: 3.1.0
info:
  title: Description Demo
paths:
  /health:
    get:
      operationId: health
      summary: Health
""",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert first.status_code == 200
    endpoints = client.get(f"/api/admin/tools/{tool_id}/endpoints", headers={"X-CSRF-Token": csrf})
    assert endpoints.status_code == 200
    assert endpoints.json()[0]["public_description"] == "Health"

    second = client.post(
        f"/api/admin/tools/{tool_id}/openapi/parse",
        json={
            "source_type": "text",
            "document_text": """
openapi: 3.1.0
info:
  title: Description Demo
paths:
  /health:
    get:
      operationId: health
      summary: Health
      description: 返回服务健康状态。
""",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert second.status_code == 200
    assert second.json()["diffs"][0]["description"] == "返回服务健康状态。"
    endpoints = client.get(f"/api/admin/tools/{tool_id}/endpoints", headers={"X-CSRF-Token": csrf})
    assert endpoints.status_code == 200
    assert endpoints.json()[0]["public_description"] == "Health"

    _accept_all_diffs(client, csrf, second.json())
    published = client.post(
        f"/api/admin/tools/{tool_id}/endpoints/bulk-publish",
        json={"confirm": True, "reason": "确认接口说明变更并发布", "max_risk_level": "medium"},
        headers={"X-CSRF-Token": csrf},
    )
    assert published.status_code == 200
    endpoints = client.get(f"/api/admin/tools/{tool_id}/endpoints", headers={"X-CSRF-Token": csrf})
    assert endpoints.status_code == 200
    assert endpoints.json()[0]["public_description"] == "返回服务健康状态。"


def test_changed_diff_single_publish_applies_operation_and_route(client: TestClient) -> None:
    csrf = _login(client, "admin", "AdminPass2026!")
    tool = client.post(
        "/api/admin/tools",
        json={
            "slug": "single-diff-demo",
            "name": "Single Diff Demo",
            "description": "验证单接口差异发布",
            "base_url": "http://127.0.0.1:18000",
            "upstream_token": "",
            "status": "active",
            "auth_type": "none",
            "health_path": "/health",
            "environment_mode": "single",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert tool.status_code == 201
    tool_id = tool.json()["id"]
    first = client.post(
        f"/api/admin/tools/{tool_id}/openapi/parse",
        json={"source_type": "text", "document_text": """
openapi: 3.1.0
paths:
  /health:
    get:
      operationId: healthV1
      summary: Health V1
"""},
        headers={"X-CSRF-Token": csrf},
    )
    assert first.status_code == 200
    _accept_all_diffs(client, csrf, first.json())
    assert client.post(f"/api/admin/import-batches/{first.json()['batch']['id']}/confirm", json={"confirm": True, "reason": "确认初始批次"}, headers={"X-CSRF-Token": csrf}).status_code == 200
    published = client.post(f"/api/admin/tools/{tool_id}/endpoints/bulk-publish", json={"confirm": True, "reason": "发布初始接口", "max_risk_level": "medium"}, headers={"X-CSRF-Token": csrf})
    assert published.status_code == 200
    endpoint_id = published.json()["published"][0]["endpoint_id"]

    second = client.post(
        f"/api/admin/tools/{tool_id}/openapi/parse",
        json={"source_type": "text", "document_text": """
openapi: 3.1.0
paths:
  /health:
    get:
      operationId: healthV2
      summary: Health V2
      description: 新的健康检查说明
"""},
        headers={"X-CSRF-Token": csrf},
    )
    assert second.status_code == 200
    diff = second.json()["diffs"][0]
    assert diff["diff_type"] == "changed"
    _accept_diff(client, csrf, diff)
    assert client.post(f"/api/admin/import-batches/{second.json()['batch']['id']}/confirm", json={"confirm": True, "reason": "确认更新批次"}, headers={"X-CSRF-Token": csrf}).status_code == 200
    single = client.patch(f"/api/admin/endpoints/{endpoint_id}/publish", json={"is_enabled": True, "confirm": True, "reason": "发布单接口变更"}, headers={"X-CSRF-Token": csrf})
    assert single.status_code == 200, single.text
    assert single.json()["operation_id"] == "healthV2"
    assert single.json()["summary"] == "Health V2"
    assert single.json()["public_description"] == "新的健康检查说明"


def test_cyclic_openapi_import_rolls_back_all_database_writes(client: TestClient) -> None:
    csrf = _login(client, "admin", "AdminPass2026!")
    tool = client.post(
        "/api/admin/tools",
        json={
            "slug": "cyclic-import-demo",
            "name": "Cyclic Import Demo",
            "description": "循环文档回滚测试",
            "base_url": "http://127.0.0.1:18000",
            "upstream_token": "",
            "status": "active",
            "auth_type": "none",
            "health_path": "/health",
            "environment_mode": "single",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert tool.status_code == 201
    tool_id = tool.json()["id"]
    response = client.post(
        f"/api/admin/tools/{tool_id}/openapi/parse",
        json={"source_type": "text", "document_text": """
openapi: 3.1.0
paths:
  /health:
    get:
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Node'
components:
  schemas:
    Node:
      $ref: '#/components/schemas/Node'
"""},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
    assert client.get(f"/api/admin/tools/{tool_id}/endpoints", headers={"X-CSRF-Token": csrf}).json() == []
    assert client.get(f"/api/admin/tools/{tool_id}/import-batches/latest", headers={"X-CSRF-Token": csrf}).json()["batch"] is None


def test_gateway_uses_single_deployment_server_without_instance_pool(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeUnavailableHealthClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeUnavailableHealthClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str, **kwargs: Any) -> httpx.Response:
            return httpx.Response(503, json={"status": "down"}, headers={"content-type": "application/json"})

    class FakeGatewayClient:
        requested_urls: list[str] = []

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeGatewayClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str, **kwargs: Any) -> httpx.Response:
            return httpx.Response(503, json={"status": "down"}, headers={"content-type": "application/json"})

        def build_request(self, method: str, url: str, **kwargs: Any) -> httpx.Request:
            return httpx.Request(method, url, params=kwargs.get("params"), headers=kwargs.get("headers"), content=b"")

        async def send(self, request: httpx.Request, stream: bool = False) -> httpx.Response:
            self.requested_urls.append(str(request.url))
            return httpx.Response(200, json={"status": "ok"}, headers={"content-type": "application/json"}, request=request)

        async def aclose(self) -> None:
            return None

    from app.routers import admin as admin_router
    from app.services import gateway_proxy as tomodd_service

    monkeypatch.setattr(admin_router.httpx, "AsyncClient", FakeUnavailableHealthClient)
    monkeypatch.setattr(tomodd_service.httpx, "AsyncClient", FakeGatewayClient)
    csrf = _login(client, "admin", "AdminPass2026!")
    tool = client.post(
        "/api/admin/tools",
        json={
            "slug": "single-upstream",
            "name": "Single Upstream",
            "description": "single deployment server",
            "base_url": "http://127.0.0.1:18000",
            "upstream_token": "",
            "status": "active",
            "auth_type": "none",
            "health_path": "/health",
            "environment_mode": "single",
            "environments": [{"name": "prod", "instance_name": "default", "base_url": "http://127.0.0.1:18000", "enabled": True, "priority": 100}],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert tool.status_code == 201
    tool_id = tool.json()["id"]
    parsed = client.post(
        f"/api/admin/tools/{tool_id}/openapi/parse",
        json={
            "source_type": "text",
            "document_text": """
openapi: 3.1.0
info:
  title: Single Upstream
paths:
  /health:
    get:
      operationId: health
      summary: health
""",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert parsed.status_code == 200
    _accept_all_diffs(client, csrf, parsed.json())
    endpoint = client.get(f"/api/admin/tools/{tool_id}/endpoints").json()[0]
    published = client.patch(
        f"/api/admin/endpoints/{endpoint['id']}/publish",
        json={"is_enabled": True, "confirm": True, "reason": "publish health"},
        headers={"X-CSRF-Token": csrf},
    )
    assert published.status_code == 200
    unhealthy = client.post(f"/api/admin/tools/{tool_id}/health/test", headers={"X-CSRF-Token": csrf})
    assert unhealthy.status_code == 200
    assert unhealthy.json()["status"] == "unavailable"
    assert "instances" not in unhealthy.json()

    registered = client.post("/api/auth/register", json={"username": "singleupstreamuser", "password": "StrongPass2026!"})
    assert registered.status_code == 201
    client.post(f"/api/admin/users/{registered.json()['id']}/approve", json={"reason": "single upstream test"}, headers={"X-CSRF-Token": csrf})
    user_csrf = _login(client, "singleupstreamuser", "StrongPass2026!")
    key = client.post("/api/me/api-keys", json={"label": "single upstream key"}, headers={"X-CSRF-Token": user_csrf})
    assert key.status_code == 201

    gateway_response = client.get("/gateway/single-upstream/health", headers={"X-API-Key": key.json()["secret"]})
    assert gateway_response.status_code == 200
    assert gateway_response.json()["status"] == "ok"
    assert FakeGatewayClient.requested_urls == ["http://127.0.0.1:18000/health"]
    csrf = _login(client, "admin", "AdminPass2026!")
    requests = client.get("/api/admin/monitor/requests?tool_slug=single-upstream", headers={"X-CSRF-Token": csrf})
    assert requests.status_code == 200
    assert requests.json()[0]["status_code"] == 200
    assert requests.json()[0]["error_code"] is None
    metrics = client.get("/api/admin/monitor/metrics?tool_slug=single-upstream", headers={"X-CSRF-Token": csrf})
    assert metrics.status_code == 200
    assert metrics.json()["requests"]["success"] == 1
    assert "upstreams" not in metrics.json()
    alerts = client.get("/api/admin/monitor/alerts?tool_slug=single-upstream", headers={"X-CSRF-Token": csrf})
    assert alerts.status_code == 200
    assert not any(item["type"] == "upstream_unavailable" for item in alerts.json())


def test_admin_bulk_publish_requires_all_diffs_decided_and_publishes_all_accepted(client: TestClient) -> None:
    csrf = _login(client, "admin", "AdminPass2026!")
    tool = client.post(
        "/api/admin/tools",
        json={
            "slug": "bulk-demo",
            "name": "Bulk Demo",
            "description": "bulk publish safe endpoints",
            "base_url": "http://127.0.0.1:18000",
            "upstream_token": "",
            "status": "active",
            "auth_type": "none",
            "health_path": "/health",
            "environment_mode": "single",
            "environments": [{"name": "prod", "instance_name": "default", "base_url": "http://127.0.0.1:18000", "enabled": True, "priority": 100}],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert tool.status_code == 201
    tool_id = tool.json()["id"]
    parsed = client.post(
        f"/api/admin/tools/{tool_id}/openapi/parse",
        json={
            "source_type": "text",
            "document_text": """
openapi: 3.1.0
info:
  title: Bulk Demo
paths:
  /health:
    get:
      operationId: health
      summary: health
      parameters:
        - name: verbose
          in: query
          required: false
          schema:
            type: boolean
          description: 是否返回详细健康信息
      responses:
        "200":
          description: ok
  /api/v1/analyze:
    post:
      operationId: analyze
      summary: analyze
      requestBody:
        content:
          application/json:
            schema:
              type: object
  /api/v1/admin/status:
    get:
      operationId: adminStatus
      summary: admin status
  /api/v1/jobs/{job_id}:
    get:
      operationId: getJob
      summary: get job
""",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert parsed.status_code == 200

    diffs = parsed.json()["diffs"]
    safe_ids = [item["id"] for item in diffs if item["gateway_path"] in {"/health", "/api/v1/analyze"}]
    blocked_ids = [item["id"] for item in diffs if item["id"] not in safe_ids]
    accepted = client.post(
        "/api/admin/diffs/bulk-decision",
        json={"diff_ids": safe_ids, "decision": "accept", "reason": "批量采用完整策略建议", "use_suggestions": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert accepted.status_code == 200
    assert len(accepted.json()["updated"]) == 2
    assert accepted.json()["failed"] == []
    blocked = client.post(
        "/api/admin/diffs/bulk-decision",
        json={"diff_ids": blocked_ids, "decision": "block", "reason": "批量阻断未放行接口"},
        headers={"X-CSRF-Token": csrf},
    )
    assert blocked.status_code == 200
    assert len(blocked.json()["updated"]) == 2

    confirmed = client.post(
        f"/api/admin/import-batches/{parsed.json()['batch']['id']}/confirm",
        json={"confirm": True, "reason": "确认批量接口治理决策"},
        headers={"X-CSRF-Token": csrf},
    )
    assert confirmed.status_code == 200

    rejected = client.post(
        f"/api/admin/tools/{tool_id}/endpoints/bulk-publish",
        json={"confirm": False, "reason": "missing confirm"},
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected.status_code == 422

    published = client.post(
        f"/api/admin/tools/{tool_id}/endpoints/bulk-publish",
        json={"confirm": True, "reason": "publish safe public endpoints", "max_risk_level": "medium"},
        headers={"X-CSRF-Token": csrf},
    )
    assert published.status_code == 200
    payload = published.json()
    assert payload["published_count"] == 2
    assert payload["skipped_count"] == 0
    assert payload["skipped"] == []

    registered = client.post("/api/auth/register", json={"username": "bulkuser", "password": "StrongPass2026!"})
    assert registered.status_code == 201
    client.post(f"/api/admin/users/{registered.json()['id']}/approve", json={"reason": "bulk docs"}, headers={"X-CSRF-Token": csrf})
    _login(client, "bulkuser", "StrongPass2026!")
    tools = client.get("/api/me/tools")
    assert tools.status_code == 200
    assert "bulk-demo" in [item["slug"] for item in tools.json()]
    endpoints = client.get("/api/me/tools/bulk-demo/endpoints")
    assert endpoints.status_code == 200
    assert sorted(item["gateway_path"] for item in endpoints.json()) == ["/api/v1/analyze", "/health"]
    csrf = _login(client, "admin", "AdminPass2026!")
    admin_endpoints = client.get(f"/api/admin/tools/{tool_id}/endpoints", headers={"X-CSRF-Token": csrf})
    assert admin_endpoints.status_code == 200
    policies = [item["route_policy"] for item in admin_endpoints.json() if item["enabled"]]
    assert {item["route_version"] for item in policies} == {1}
    assert all(item["max_request_bytes"] > 0 and item["max_response_bytes"] > 0 for item in policies)
    assert any(item["allow_retry"] is True for item in policies)
    audits = client.get("/api/admin/audit?action=routes_bulk_published", headers={"X-CSRF-Token": csrf})
    assert audits.status_code == 200
    assert audits.json()[0]["target_id"] == tool_id


def test_admin_bulk_publish_skips_explicitly_accepted_blocker_diff(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def build_request(self, method: str, url: str, **kwargs: Any) -> httpx.Request:
            return httpx.Request(method, url, **kwargs)

        async def send(self, request: httpx.Request, stream: bool = False) -> httpx.Response:
            return httpx.Response(200, json={"path": request.url.path, "method": request.method}, headers={"content-type": "application/json"})

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(gateway_proxy.httpx, "AsyncClient", FakeAsyncClient)
    csrf = _login(client, "admin", "AdminPass2026!")
    tool = client.post(
        "/api/admin/tools",
        json={
            "slug": "accepted-blocker-demo",
            "name": "Accepted Blocker Demo",
            "description": "bulk publish accepted blocker endpoint",
            "base_url": "http://127.0.0.1:18000",
            "upstream_token": "",
            "status": "active",
            "auth_type": "none",
            "health_path": "/health",
            "environment_mode": "single",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert tool.status_code == 201
    tool_id = tool.json()["id"]
    parsed = client.post(
        f"/api/admin/tools/{tool_id}/openapi/parse",
        json={
            "source_type": "text",
            "document_text": """
openapi: 3.1.0
info:
  title: Accepted Blocker Demo
paths:
  /health:
    get:
      operationId: health
      summary: health
  /api/v1/admin/status:
    get:
      operationId: adminStatus
      summary: admin status
""",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert parsed.status_code == 200
    blocker_diff = next(item for item in parsed.json()["diffs"] if item["gateway_path"] == "/api/v1/admin/status")
    assert blocker_diff["risk_level"] == "blocker"

    _accept_all_diffs(client, csrf, parsed.json())

    published = client.post(
        f"/api/admin/tools/{tool_id}/endpoints/bulk-publish",
        json={"confirm": True, "reason": "发布已放行接口", "max_risk_level": "medium"},
        headers={"X-CSRF-Token": csrf},
    )
    assert published.status_code == 200
    payload = published.json()
    assert payload["published_count"] == 1
    assert payload["skipped_count"] == 1
    assert [item["gateway_path"] for item in payload["published"]] == ["/health"]
    assert payload["skipped"][0]["gateway_path"] == "/api/v1/admin/status"

    registered = client.post("/api/auth/register", json={"username": "acceptedblockeruser", "password": "StrongPass2026!"})
    assert registered.status_code == 201
    approved = client.post(
        f"/api/admin/users/{registered.json()['id']}/approve",
        json={"reason": "验证新工具发布出口"},
        headers={"X-CSRF-Token": csrf},
    )
    assert approved.status_code == 200
    user_csrf = _login(client, "acceptedblockeruser", "StrongPass2026!")
    key = client.post("/api/me/api-keys", json={"label": "新工具调用 Key"}, headers={"X-CSRF-Token": user_csrf})
    assert key.status_code == 201

    tools = client.get("/api/me/tools")
    assert tools.status_code == 200
    assert "accepted-blocker-demo" in [item["slug"] for item in tools.json()]
    endpoints = client.get("/api/me/tools/accepted-blocker-demo/endpoints")
    assert endpoints.status_code == 200
    assert sorted(item["gateway_path"] for item in endpoints.json()) == ["/health"]
    gateway = client.get("/gateway/accepted-blocker-demo/api/v1/admin/status", headers={"X-API-Key": key.json()["secret"]})
    assert gateway.status_code == 404


def test_blocking_published_endpoint_immediately_removes_data_plane_route(client: TestClient) -> None:
    admin_csrf = _login(client, "admin", "AdminPass2026!")
    tool = client.post(
        "/api/admin/tools",
        json={
            "slug": "block-route-demo",
            "name": "Block Route Demo",
            "description": "验证阻断操作会立即关闭线上路由",
            "base_url": "http://127.0.0.1:18000",
            "upstream_token": "",
            "status": "active",
            "auth_type": "none",
            "health_path": "/health",
            "environment_mode": "single",
        },
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert tool.status_code == 201
    tool_id = tool.json()["id"]
    parsed = client.post(
        f"/api/admin/tools/{tool_id}/openapi/parse",
        json={
            "source_type": "text",
            "document_text": """
openapi: 3.1.0
info:
  title: Block Route Demo
paths:
  /health:
    get:
      operationId: health
      summary: health
""",
        },
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert parsed.status_code == 200
    _accept_all_diffs(client, admin_csrf, parsed.json())
    published = client.post(
        f"/api/admin/tools/{tool_id}/endpoints/bulk-publish",
        json={"confirm": True, "reason": "发布阻断链路验证接口", "max_risk_level": "medium"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert published.status_code == 200
    endpoint_id = published.json()["published"][0]["endpoint_id"]

    blocked = client.post(
        f"/api/admin/endpoints/{endpoint_id}/block",
        json={"reason": "验证管理员紧急阻断线上接口"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert blocked.status_code == 200
    assert blocked.json()["status"] == "blocked"
    assert blocked.json()["enabled"] is False

    remediated = client.patch(
        f"/api/admin/endpoints/{endpoint_id}",
        json={"status": "candidate", "reason": "尝试绕过阻断状态"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert remediated.status_code == 200
    assert remediated.json()["status"] == "candidate"
    assert remediated.json()["enabled"] is False

    invalid_blocker_state = client.patch(
        f"/api/admin/endpoints/{endpoint_id}",
        json={"status": "candidate", "risk_level": "blocker", "reason": "验证阻断级风险状态约束"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert invalid_blocker_state.status_code == 422

    registered = client.post("/api/auth/register", json={"username": "blockrouteuser", "password": "StrongPass2026!"})
    assert registered.status_code == 201
    approved = client.post(
        f"/api/admin/users/{registered.json()['id']}/approve",
        json={"reason": "验证阻断后的用户可见性"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert approved.status_code == 200
    user_csrf = _login(client, "blockrouteuser", "StrongPass2026!")
    key = client.post("/api/me/api-keys", json={"label": "阻断验证 Key"}, headers={"X-CSRF-Token": user_csrf})
    assert key.status_code == 201
    endpoints = client.get("/api/me/tools/block-route-demo/endpoints")
    assert endpoints.status_code == 200
    assert endpoints.json() == []
    gateway = client.get("/gateway/block-route-demo/health", headers={"X-API-Key": key.json()["secret"]})
    assert gateway.status_code == 404


def test_admin_bulk_publish_publishes_all_accepted_import_diffs(client: TestClient) -> None:
    csrf = _login(client, "admin", "AdminPass2026!")
    tool = client.post(
        "/api/admin/tools",
        json={
            "slug": "all-accepted-demo",
            "name": "All Accepted Demo",
            "description": "all accepted endpoints should publish",
            "base_url": "http://127.0.0.1:18000",
            "upstream_token": "",
            "status": "active",
            "auth_type": "none",
            "health_path": "/health",
            "environment_mode": "single",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert tool.status_code == 201
    tool_id = tool.json()["id"]
    parsed = client.post(
        f"/api/admin/tools/{tool_id}/openapi/parse",
        json={
            "source_type": "text",
            "document_text": """
openapi: 3.1.0
info:
  title: All Accepted Demo
paths:
  /api/v1/admin/metrics:
    get:
      operationId: adminMetrics
      summary: admin metrics
  /api/v1/jobs:
    get:
      operationId: listJobs
      summary: list jobs
  /api/v1/jobs/{job_id}:
    delete:
      operationId: deleteJob
      summary: delete job
""",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert parsed.status_code == 200
    diffs = parsed.json()["diffs"]
    assert len(diffs) == 3
    assert all(item["risk_level"] == "blocker" for item in diffs)

    _accept_all_diffs(client, csrf, parsed.json())

    published = client.post(
        f"/api/admin/tools/{tool_id}/endpoints/bulk-publish",
        json={"confirm": True, "reason": "发布全部放行接口", "max_risk_level": "medium"},
        headers={"X-CSRF-Token": csrf},
    )
    assert published.status_code == 200
    payload = published.json()
    assert payload["published_count"] == 0
    assert payload["skipped_count"] == 3
    assert payload["published"] == []
    assert sorted(item["gateway_path"] for item in payload["skipped"]) == [
        "/api/v1/admin/metrics",
        "/api/v1/jobs",
        "/api/v1/jobs/{job_id}",
    ]


def test_user_quickstart_uses_published_routes_without_leaking_upstream(client: TestClient) -> None:
    admin_csrf = _login(client, "admin", "AdminPass2026!")
    tool = client.post(
        "/api/admin/tools",
        json={
            "slug": "quick-demo",
            "name": "Quick Demo",
            "description": "用户快速接入测试工具",
            "base_url": "http://127.0.0.1:18000",
            "upstream_token": "secret-upstream-token",
            "status": "active",
            "auth_type": "bearer",
            "health_path": "/health",
            "environment_mode": "single",
            "environments": [{"name": "prod", "instance_name": "default", "base_url": "http://127.0.0.1:18000", "enabled": True, "priority": 100}],
        },
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert tool.status_code == 201
    tool_id = tool.json()["id"]

    parsed = client.post(
        f"/api/admin/tools/{tool_id}/openapi/parse",
        json={
            "source_type": "text",
            "document_text": """
openapi: 3.1.0
info:
  title: Quick Demo
paths:
  /health:
    get:
      operationId: health
      summary: health
      parameters:
        - name: verbose
          in: query
          required: false
          schema:
            type: boolean
          description: 是否返回详细健康信息
      responses:
        "200":
          description: ok
  /api/v1/analyze:
    post:
      operationId: analyze
      summary: analyze
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                value:
                  type: string
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema:
                type: object
                properties:
                  result:
                    type: string
""",
        },
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert parsed.status_code == 200
    _accept_all_diffs(client, admin_csrf, parsed.json())
    published = client.post(
        f"/api/admin/tools/{tool_id}/endpoints/bulk-publish",
        json={"confirm": True, "reason": "发布用户快速接入样例", "max_risk_level": "medium"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert published.status_code == 200
    assert published.json()["published_count"] == 2

    registered = client.post("/api/auth/register", json={"username": "quickuser", "password": "StrongPass2026!"})
    assert registered.status_code == 201
    approved = client.post(
        f"/api/admin/users/{registered.json()['id']}/approve",
        json={"reason": "快速接入测试"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert approved.status_code == 200
    user_csrf = _login(client, "quickuser", "StrongPass2026!")
    created_key = client.post("/api/me/api-keys", json={"label": "快速接入 Key"}, headers={"X-CSRF-Token": user_csrf})
    assert created_key.status_code == 201

    response = client.get("/api/me/quickstart")
    assert response.status_code == 200
    payload = response.json()
    assert payload["gateway_pattern"] == "/gateway/{tool_slug}{gateway_path}"
    assert payload["auth_header"] == "X-API-Key"
    assert payload["active_key_count"] == 1
    assert payload["active_key_prefixes"][0].startswith("agw_")
    assert payload["published_tool_count"] == 1
    assert payload["published_endpoint_count"] == 2
    assert payload["sample_endpoint"]["tool_slug"] == "quick-demo"
    assert payload["sample_endpoint"]["method"] == "GET"
    assert payload["sample_endpoint"]["gateway_path"] == "/health"
    assert payload["tools"][0]["published_endpoint_count"] == 2
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert "127.0.0.1:18000" not in serialized
    assert "secret-upstream-token" not in serialized
    assert created_key.json()["secret"].lower() not in serialized

    docs = client.get("/api/me/api-docs")
    assert docs.status_code == 200
    docs_payload = docs.json()
    assert docs_payload["gateway_pattern"] == "/gateway/{tool_slug}{gateway_path}"
    assert docs_payload["auth_header"] == "X-API-Key"
    assert docs_payload["total_tools"] == 1
    assert docs_payload["total_endpoints"] == 2
    doc_tool = docs_payload["tools"][0]
    assert doc_tool["slug"] == "quick-demo"
    assert doc_tool["gateway_base_path"] == "/gateway/quick-demo"
    health_doc = next(item for item in doc_tool["endpoints"] if item["gateway_path"] == "/health")
    assert health_doc["parameters"][0]["name"] == "verbose"
    analyze_doc = next(item for item in doc_tool["endpoints"] if item["gateway_path"] == "/api/v1/analyze")
    assert "value" in json.dumps(analyze_doc["request_body"], ensure_ascii=False)
    assert "result" in json.dumps(analyze_doc["responses"], ensure_ascii=False)
    assert "upstream_path" not in analyze_doc
    docs_serialized = json.dumps(docs_payload, ensure_ascii=False).lower()
    assert "127.0.0.1:18000" not in docs_serialized
    assert "secret-upstream-token" not in docs_serialized
    assert created_key.json()["secret"].lower() not in docs_serialized


def test_public_registration_closes_without_smtp(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_from", "")
    config = client.get("/api/public/config")
    assert config.status_code == 200
    assert config.json()["registration_enabled"] is False
    assert config.json()["password_recovery_enabled"] is False
    assert config.json()["email_change_enabled"] is False
    response = client.post(
        "/api/auth/register",
        json={"username": "closed", "email": "closed@example.com", "password": "StrongPass2026!"},
    )
    assert response.status_code == 503


def test_action_tokens_are_hashed_single_use_and_reissued_tokens_replace_old(client: TestClient) -> None:
    created = client.post(
        "/api/auth/register",
        json={"username": "tokenuser", "email": "token@example.com", "password": "StrongPass2026!"},
    )
    assert created.status_code == 201
    database_override = app.dependency_overrides[get_db]()
    session = next(database_override)
    try:
        old_token = issue_action_token(
            session,
            user_id=created.json()["id"],
            purpose="reset_password",
            target_email="token@example.com",
            lifetime=timedelta(minutes=30),
        )
        new_token = issue_action_token(
            session,
            user_id=created.json()["id"],
            purpose="reset_password",
            target_email="token@example.com",
            lifetime=timedelta(minutes=30),
        )
        session.commit()
        assert old_token != new_token
        assert get_valid_action_token(session, token=old_token, purpose="reset_password") is None
        current = get_valid_action_token(session, token=new_token, purpose="reset_password")
        assert current is not None
        stored = session.scalars(select(AuthActionToken)).all()
        assert all(item.token_hash not in {old_token, new_token} for item in stored)
        consume_action_token(current)
        session.commit()
        assert get_valid_action_token(session, token=new_token, purpose="reset_password") is None
    finally:
        database_override.close()


def test_email_outbox_failure_is_recorded_without_rolling_back_registration(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "email_outbox_max_attempts", 1)

    class FailingSmtp:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise OSError("smtp unavailable")

    monkeypatch.setattr("app.services.email_delivery.smtplib.SMTP", FailingSmtp)
    created = client.post(
        "/api/auth/register",
        json={"username": "mailuser", "email": "mail@example.com", "password": "StrongPass2026!"},
    )
    assert created.status_code == 201
    database_override = app.dependency_overrides[get_db]()
    session = next(database_override)
    try:
        assert deliver_due_email(session) is True
        session.expire_all()
        outbox = session.scalar(select(EmailOutbox).where(EmailOutbox.recipient == "mail@example.com"))
        assert outbox is not None
        assert outbox.status == "failed"
        assert outbox.attempts == 1
        assert outbox.last_error == "SMTP_DELIVERY_FAILED:OSError:connect"
        assert session.get(User, created.json()["id"]) is not None
    finally:
        database_override.close()


def test_key_expiry_contract_repeat_disable_and_admin_password_reset(client: TestClient) -> None:
    registered = client.post(
        "/api/auth/register",
        json={"username": "lifecycle", "email": "life@example.com", "password": "StrongPass2026!"},
    )
    admin_csrf = _login(client, "admin", "AdminPass2026!")
    approved = client.post(
        f"/api/admin/users/{registered.json()['id']}/approve",
        json={"reason": "生命周期测试"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert approved.status_code == 200
    user_csrf = _login(client, "lifecycle", "StrongPass2026!")
    default_key = client.post("/api/me/api-keys", json={"label": "默认期限"}, headers={"X-CSRF-Token": user_csrf})
    permanent_key = client.post(
        "/api/me/api-keys",
        json={"label": "永久期限", "permanent": True},
        headers={"X-CSRF-Token": user_csrf},
    )
    conflict = client.post(
        "/api/me/api-keys",
        json={"label": "冲突期限", "expires_at": "2030-01-01T00:00:00Z", "expires_in_days": 30},
        headers={"X-CSRF-Token": user_csrf},
    )
    assert default_key.status_code == 201 and default_key.json()["expires_at"] is not None
    assert permanent_key.status_code == 201 and permanent_key.json()["expires_at"] is None
    assert conflict.status_code == 422
    key_id = default_key.json()["id"]
    assert client.patch(f"/api/me/api-keys/{key_id}/disable", headers={"X-CSRF-Token": user_csrf}).status_code == 200
    assert client.patch(f"/api/me/api-keys/{key_id}/disable", headers={"X-CSRF-Token": user_csrf}).status_code == 409

    admin_csrf = _login(client, "admin", "AdminPass2026!")
    reset = client.post(
        f"/api/admin/users/{registered.json()['id']}/reset-password",
        json={"reason": "用户确认无法找回密码"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert reset.status_code == 200
    temporary_password = reset.json()["temporary_password"]
    assert client.post("/api/auth/login", json={"username": "lifecycle", "password": "StrongPass2026!"}).status_code == 401
    temporary_login = client.post("/api/auth/login", json={"username": "lifecycle", "password": temporary_password})
    assert temporary_login.status_code == 200
    assert temporary_login.json()["must_change_password"] is True
    notifications = client.get("/api/me/notifications?page=1&page_size=20")
    assert notifications.status_code == 403


def test_call_csv_and_notifications_are_user_isolated_and_formula_safe(client: TestClient) -> None:
    first = client.post(
        "/api/auth/register",
        json={"username": "exportone", "email": "one@example.com", "password": "StrongPass2026!"},
    ).json()
    second = client.post(
        "/api/auth/register",
        json={"username": "exporttwo", "email": "two@example.com", "password": "StrongPass2026!"},
    ).json()
    admin_csrf = _login(client, "admin", "AdminPass2026!")
    for item in (first, second):
        assert client.post(
            f"/api/admin/users/{item['id']}/approve",
            json={"reason": "隔离测试"},
            headers={"X-CSRF-Token": admin_csrf},
        ).status_code == 200
    database_override = app.dependency_overrides[get_db]()
    session = next(database_override)
    try:
        session.add_all([
            GatewayRequest(id="=formula-one", user_id=first["id"], tool_slug="=tool", method="GET", path="+SUM(1,1)", status_code=200),
            GatewayRequest(id="second-private", user_id=second["id"], tool_slug="private", method="GET", path="/private", status_code=500),
        ])
        add_notification(session, user_id=first["id"], kind="security", title="仅用户一", body="one-only")
        add_notification(session, user_id=second["id"], kind="security", title="仅用户二", body="two-only")
        session.commit()
    finally:
        database_override.close()
    _login(client, "exportone", "StrongPass2026!")
    exported = client.get("/api/me/calls/export.csv")
    assert exported.status_code == 200
    assert "'=formula-one" in exported.text
    assert "'=tool" in exported.text
    assert "'+SUM(1,1)" in exported.text
    assert "second-private" not in exported.text
    notifications = client.get("/api/me/notifications?page=1&page_size=20")
    assert notifications.status_code == 200
    serialized = json.dumps(notifications.json(), ensure_ascii=False)
    assert "仅用户一" in serialized
    assert "仅用户二" not in serialized


def test_platform_email_settings_encrypt_password_and_queue_builtin_test_email(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csrf = _login(client, "admin", "AdminPass2026!")
    payload = {
        "smtp_enabled": True,
        "smtp_host": "smtp.example.test",
        "smtp_port": 587,
        "smtp_username": "mailer@example.test",
        "smtp_password": "smtp-secret-password",
        "smtp_from_email": "gateway@example.test",
        "smtp_from_name": "科研 API 平台",
        "smtp_use_tls": True,
    }
    saved = client.put("/api/admin/settings/email", json=payload, headers={"X-CSRF-Token": csrf})
    assert saved.status_code == 200, saved.text
    assert saved.json()["smtp_configured"] is True
    assert saved.json()["smtp_password_configured"] is True
    assert "smtp_password_ciphertext" not in saved.json()
    assert "smtp_password" not in saved.json()

    database_override = app.dependency_overrides[get_db]()
    session = next(database_override)
    try:
        row = session.get(PlatformSettings, 1)
        assert row is not None
        assert row.smtp_password_ciphertext != payload["smtp_password"]
        assert decrypt_secret(row.smtp_password_ciphertext) == payload["smtp_password"]
        audit_json = "\n".join(session.scalars(select(AdminAuditEvent.detail_json)).all())
        assert payload["smtp_password"] not in audit_json
    finally:
        database_override.close()


    preserve = client.put(
        "/api/admin/settings/email",
        json={**payload, "smtp_password": None},
        headers={"X-CSRF-Token": csrf},
    )
    assert preserve.status_code == 200
    assert preserve.json()["smtp_password_configured"] is True

    monkeypatch.setattr("app.routers.admin.test_smtp_connection", lambda session: None)
    tested = client.post("/api/admin/settings/email/test-connection", headers={"X-CSRF-Token": csrf})
    assert tested.status_code == 200
    queued = client.post(
        "/api/admin/settings/email/test-message",
        json={"recipient": "recipient@example.test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert queued.status_code == 202
    database_override = app.dependency_overrides[get_db]()
    session = next(database_override)
    try:
        outbox = session.get(EmailOutbox, queued.json()["outbox_id"])
        assert outbox is not None
        body = decrypt_secret(outbox.body_ciphertext)
        assert "平台邮件配置测试邮件" in body
        assert "recipient@example.test" in body
    finally:
        database_override.close()


def test_platform_email_settings_normalizes_implicit_tls_port(client: TestClient) -> None:
    """保存 465 端口时必须关闭 STARTTLS，避免后台配置语义冲突。"""

    csrf = _login(client, "admin", "AdminPass2026!")
    response = client.put(
        "/api/admin/settings/email",
        json={
            "smtp_enabled": True,
            "smtp_host": "smtp.example.test",
            "smtp_port": 465,
            "smtp_username": "mailer@example.test",
            "smtp_password": "smtp-secret-password",
            "smtp_from_email": "gateway@example.test",
            "smtp_from_name": "API Gateway",
            "smtp_use_tls": True,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["smtp_port"] == 465
    assert response.json()["smtp_use_tls"] is False


def test_platform_default_limit_is_copied_and_user_override_is_audited(client: TestClient) -> None:
    csrf = _login(client, "admin", "AdminPass2026!")
    saved = client.put(
        "/api/admin/settings/user-defaults",
        json={"rate_limit_per_minute": 7},
        headers={"X-CSRF-Token": csrf},
    )
    assert saved.status_code == 200
    assert saved.json()["default_user_rate_limit_per_minute"] == 7

    registered = client.post(
        "/api/auth/register",
        json={"username": "defaultlimit", "email": "defaultlimit@example.com", "password": "StrongPass2026!"},
    )
    assert registered.status_code == 201
    imported = client.post(
        "/api/admin/users/import",
        files={"file": ("users.csv", b"username,password,email\nimportlimit,StrongPass2026!,importlimit@example.com\n", "text/csv")},
        headers={"X-CSRF-Token": csrf},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["accepted"] == 1

    database_override = app.dependency_overrides[get_db]()
    session = next(database_override)
    try:
        registered_row = session.get(User, registered.json()["id"])
        imported_row = session.scalar(select(User).where(User.username == "importlimit"))
        assert registered_row is not None and registered_row.rate_limit_per_minute == 7
        assert imported_row is not None and imported_row.rate_limit_per_minute == 7
    finally:
        database_override.close()

    updated = client.patch(
        f"/api/admin/users/{registered.json()['id']}/rate-limit",
        json={"rate_limit_per_minute": 0},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200
    assert updated.json()["rate_limit_per_minute"] == 0
    invalid = client.patch(
        f"/api/admin/users/{registered.json()['id']}/rate-limit",
        json={"rate_limit_per_minute": 10_001},
        headers={"X-CSRF-Token": csrf},
    )
    assert invalid.status_code == 422
    audit = client.get("/api/admin/audit?action=user_rate_limit_updated&page=1&page_size=20")
    assert audit.status_code == 200
    assert audit.json()["total"] == 1
    assert audit.json()["items"][0]["detail"]["rate_limit_per_minute"] == 0


def test_public_gateway_setting_drives_public_config_and_user_docs(client: TestClient) -> None:
    csrf = _login(client, "admin", "AdminPass2026!")
    invalid = client.put(
        "/api/admin/settings/gateway",
        json={"public_gateway_base_url": "https://api.example.test/gateway"},
        headers={"X-CSRF-Token": csrf},
    )
    assert invalid.status_code == 422

    saved = client.put(
        "/api/admin/settings/gateway",
        json={"public_gateway_base_url": "https://api.example.test/"},
        headers={"X-CSRF-Token": csrf},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["public_gateway_base_url"] == "https://api.example.test"
    assert saved.json()["gateway_source"] == "database"

    public_config = client.get("/api/public/config")
    assert public_config.status_code == 200
    assert public_config.json()["gateway_base_url"] == "https://api.example.test"
    assert client.get("/api/me/quickstart").json()["gateway_base_url"] == "https://api.example.test"
    assert client.get("/api/me/api-docs").json()["gateway_base_url"] == "https://api.example.test"

    audit = client.get("/api/admin/audit?action=platform_gateway_settings_updated&page=1")
    assert audit.status_code == 200
    assert audit.json()["total"] == 1


def test_tool_configuration_update_is_versioned_tested_and_reversible(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csrf = _login(client, "admin", "AdminPass2026!")
    created = client.post(
        "/api/admin/tools",
        json={
            "slug": "editable-tool",
            "name": "Editable Tool",
            "description": "工具配置闭环测试",
            "base_url": "http://127.0.0.1:18000",
            "upstream_token": "initial-upstream-secret",
            "status": "active",
            "auth_type": "bearer",
            "health_path": "/health",
            "network_isolation_confirmed": True,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    tool_id = created.json()["id"]
    current = client.get(f"/api/admin/tools/{tool_id}/configuration")
    assert current.status_code == 200
    assert current.json()["base_url"] == "http://127.0.0.1:18000"
    assert current.json()["token_configured"] is True
    assert "initial-upstream-secret" not in current.text
    assert "token_ciphertext" not in current.text

    def update_payload(base_url: str, expected_revision: int) -> dict[str, Any]:
        item = current.json()
        return {
            "name": item["name"],
            "description": item["description"],
            "status": item["status"],
            "default_gateway_prefix": item["default_gateway_prefix"],
            "base_url": base_url,
            "health_path": item["health_path"],
            "auth_type": item["auth_type"],
            "token_label": item["token_label"],
            "upstream_token": "",
            "clear_upstream_token": False,
            "rate_limit_per_minute": item["rate_limit_per_minute"],
            "network_zone": item["network_zone"],
            "verify_tls": item["verify_tls"],
            "retry_count": item["retry_count"],
            "connect_timeout_seconds": item["connect_timeout_seconds"],
            "request_timeout_seconds": item["request_timeout_seconds"],
            "network_isolation_mode": item["network_isolation_mode"],
            "network_isolation_note": item["network_isolation_note"],
            "network_isolation_confirmed": item["network_isolation_confirmed"],
            "expected_revision": expected_revision,
            "reason": "迁移到新的生产实例",
            "confirm": True,
        }

    async def unhealthy(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"status": "unavailable", "reachable": False, "status_code": None, "path": "/health"}

    monkeypatch.setattr("app.routers.admin.check_tool_health", unhealthy)
    rejected = client.patch(
        f"/api/admin/tools/{tool_id}/configuration",
        json=update_payload("http://127.0.0.1:18001", 1),
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected.status_code == 422
    unchanged = client.get(f"/api/admin/tools/{tool_id}/configuration").json()
    assert unchanged["base_url"] == "http://127.0.0.1:18000"
    assert unchanged["config_revision"] == 1

    async def healthy(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"status": "ok", "reachable": True, "status_code": 200, "path": "/health"}

    monkeypatch.setattr("app.routers.admin.check_tool_health", healthy)
    updated = client.patch(
        f"/api/admin/tools/{tool_id}/configuration",
        json=update_payload("http://127.0.0.1:18001", 1),
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["config_revision"] == 2
    assert updated.json()["base_url"] == "http://127.0.0.1:18001"
    assert updated.json()["token_configured"] is True

    stale = client.patch(
        f"/api/admin/tools/{tool_id}/configuration",
        json=update_payload("http://127.0.0.1:18002", 1),
        headers={"X-CSRF-Token": csrf},
    )
    assert stale.status_code == 409

    history = client.get(f"/api/admin/tools/{tool_id}/configuration/revisions")
    assert history.status_code == 200
    assert history.json()[0]["config_revision"] == 1
    rollback = client.post(
        f"/api/admin/tools/{tool_id}/configuration/rollback",
        json={
            "expected_revision": 2,
            "revision_id": history.json()[0]["id"],
            "reason": "验证历史配置回滚",
            "confirm": True,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["config_revision"] == 3
    assert rollback.json()["base_url"] == "http://127.0.0.1:18000"
    assert rollback.json()["token_configured"] is True

    audit = client.get("/api/admin/audit?category=tools&page=1&page_size=100")
    assert audit.status_code == 200
    assert "initial-upstream-secret" not in audit.text
    assert "token_ciphertext" not in audit.text

