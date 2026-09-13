from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import ApiEndpoint, ApiKey, ExternalResource, GatewayRequest, IdempotencyRecord, KeyStatus, PublishedRoute, ResourceKind, Tool, ToolUpstream, ToolUpstreamInstance, User
from app.routers import portal
from app.services import gateway_proxy
from app.services.gateway_proxy import proxy_gateway_request
from app.services.idempotency import claim_idempotency_record
from app.services.resource_policies import suggest_access_policy
from app.services.security import encrypt_secret, hash_idempotency_key


class RequestLike:
    """只实现 Gateway 代理所需的 Request 属性，避免测试依赖真实 ASGI 服务。"""

    def __init__(
        self,
        *,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        query_params: dict[str, str] | None = None,
    ) -> None:
        self.method = method
        self.path_params = {"path": path.lstrip("/")}
        self.headers = headers or {}
        self.query_params = query_params or {}
        self._body = body

    async def stream(self) -> AsyncIterator[bytes]:
        if self._body:
            midpoint = max(1, len(self._body) // 2)
            yield self._body[:midpoint]
            yield self._body[midpoint:]
        else:
            yield b""


class FakeAsyncClient:
    """httpx.AsyncClient 的最小替身，用于验证代理请求和模拟上游响应。"""

    last_request: httpx.Request | None = None
    response_factory: Callable[[httpx.Request], httpx.Response] | None = None
    raised_error: Exception | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.closed = False

    def build_request(self, method: str, url: str, **kwargs: Any) -> httpx.Request:
        return httpx.Request(method, url, **kwargs)

    async def send(self, request: httpx.Request, stream: bool = False) -> httpx.Response:
        FakeAsyncClient.last_request = request
        if FakeAsyncClient.raised_error:
            raise FakeAsyncClient.raised_error
        assert FakeAsyncClient.response_factory is not None
        return FakeAsyncClient.response_factory(request)

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as current:
        yield current


@pytest.fixture(autouse=True)
def reset_fake_client(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAsyncClient.last_request = None
    FakeAsyncClient.response_factory = None
    FakeAsyncClient.raised_error = None
    monkeypatch.setattr(gateway_proxy.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(portal.httpx, "AsyncClient", FakeAsyncClient)


def seed_gateway(
    session: Session,
    *,
    method: str,
    path: str,
    content_types: list[str] | None = None,
) -> tuple[Tool, ToolUpstream, User, ApiKey]:
    safe_suffix = path.strip("/").replace("/", "-").replace("{", "").replace("}", "")
    user = User(username=f"user-{method}-{safe_suffix}", password_hash="hash", must_change_password=False)
    tool = Tool(slug="tomodd", name="tomoDD")
    session.add_all([user, tool])
    session.flush()
    api_key = ApiKey(user_id=user.id, label="测试 Key", prefix="agw_test", secret_hash=f"hash-{method}-{path}", scopes="tomodd:*", status=KeyStatus.ACTIVE.value)
    upstream = ToolUpstream(tool_id=tool.id, base_url="http://127.0.0.1:18000", token_ciphertext="")
    endpoint = ApiEndpoint(
        tool_id=tool.id,
        method=method,
        upstream_path=path,
        operation_id="op",
        summary="测试接口",
        content_types=json.dumps(content_types or []),
        status="published",
    )
    suggestion = suggest_access_policy(
        method=method,
        path=path,
        summary="测试接口",
        responses={"job_id": "string", "artifact_id": "string"} if "/jobs" in path else {},
    )
    route = PublishedRoute(
        tool_id=tool.id,
        endpoint_id=endpoint.id,
        method=method,
        gateway_path=path,
        upstream_path=path,
        access_mode=str(suggestion["access_mode"]),
        resource_policy_json=json.dumps(suggestion["rules"], ensure_ascii=False),
    )
    session.add_all([api_key, upstream, endpoint])
    session.flush()
    route.endpoint_id = endpoint.id
    session.add(route)
    session.flush()
    return tool, upstream, user, api_key


def test_resource_collection_list_defaults_to_owner_filter() -> None:
    suggestion = suggest_access_policy(
        method="GET",
        path="/api/v1/jobs",
        summary="List Jobs",
        risk_flags={"global_list_endpoint", "resource_operation"},
    )

    assert suggestion["access_mode"] == "owner"
    assert suggestion["complete"] is True
    assert suggestion["rules"]["list_filter"] == {
        "items_selectors": ["$", "$.items", "$.jobs"],
        "id_selectors": ["$.job_id", "$.id"],
        "resource_kind": "job",
    }


def test_proxy_renders_template_path_and_records_success(session: Session) -> None:
    tool, upstream, user, api_key = seed_gateway(session, method="GET", path="/api/v1/jobs/{job_id}")
    session.add(ExternalResource(tool_id=tool.id, owner_user_id=user.id, kind=ResourceKind.JOB.value, upstream_id="job-42"))
    session.flush()

    def upstream_response(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/jobs/job-42"
        return httpx.Response(200, json={"job_id": "job-42", "status": "succeeded", "artifacts": [{"artifact_id": "art-1", "name": "result.zip"}]})

    FakeAsyncClient.response_factory = upstream_response
    response = asyncio.run(
        proxy_gateway_request(
            request=RequestLike(method="GET", path="/api/v1/jobs/job-42"),  # type: ignore[arg-type]
            session=session,
            tool=tool,
            upstream=upstream,
            api_key=api_key,
            user_id=user.id,
            request_id="req-success",
        )
    )

    assert response.status_code == 200
    recorded = session.get(GatewayRequest, "req-success")
    assert recorded is not None and recorded.error_code is None
    artifact = session.scalar(select(ExternalResource).where(ExternalResource.kind == ResourceKind.ARTIFACT.value))
    assert artifact is not None and artifact.upstream_id == "art-1" and artifact.owner_user_id == user.id


def test_authenticated_policy_does_not_apply_resource_ownership(session: Session) -> None:
    tool, upstream, user, api_key = seed_gateway(session, method="GET", path="/api/v1/jobs/{job_id}")
    tool.slug = "demo"
    route = session.scalar(select(PublishedRoute).where(PublishedRoute.tool_id == tool.id))
    assert route is not None
    route.access_mode = "authenticated"
    route.resource_policy_json = "{}"

    def upstream_response(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/jobs/foreign-job"
        return httpx.Response(200, json={"job_id": "foreign-job", "status": "ok"})

    FakeAsyncClient.response_factory = upstream_response
    response = asyncio.run(
        proxy_gateway_request(
            request=RequestLike(method="GET", path="/api/v1/jobs/foreign-job"),  # type: ignore[arg-type]
            session=session,
            tool=tool,
            upstream=upstream,
            api_key=api_key,
            user_id=user.id,
            request_id="req-generic",
        )
    )

    assert response.status_code == 200
    assert session.scalar(select(ExternalResource)) is None


def test_proxy_wraps_non_json_upstream_error_and_records_it(session: Session) -> None:
    tool, upstream, user, api_key = seed_gateway(session, method="GET", path="/api/v1/jobs/{job_id}")
    session.add(ExternalResource(tool_id=tool.id, owner_user_id=user.id, kind=ResourceKind.JOB.value, upstream_id="job-500"))
    session.flush()
    FakeAsyncClient.response_factory = lambda request: httpx.Response(500, content=b"<html>boom</html>", headers={"content-type": "text/html"})

    response = asyncio.run(
        proxy_gateway_request(
            request=RequestLike(method="GET", path="/api/v1/jobs/job-500"),  # type: ignore[arg-type]
            session=session,
            tool=tool,
            upstream=upstream,
            api_key=api_key,
            user_id=user.id,
            request_id="req-html-error",
        )
    )

    assert response.status_code == 500
    assert json.loads(response.body)["code"] == "UPSTREAM_ERROR"
    recorded = session.get(GatewayRequest, "req-html-error")
    assert recorded is not None and recorded.error_code == "UPSTREAM_ERROR"


def test_proxy_invalid_json_response_does_not_create_resource_mapping(session: Session) -> None:
    tool, upstream, user, api_key = seed_gateway(session, method="POST", path="/api/v1/datasets", content_types=["application/json"])
    FakeAsyncClient.response_factory = lambda request: httpx.Response(200, content=b"{not-json", headers={"content-type": "application/json"})

    response = asyncio.run(
        proxy_gateway_request(
            request=RequestLike(method="POST", path="/api/v1/datasets", headers={"content-type": "application/json"}, body=b'{"name":"bad-json"}'),  # type: ignore[arg-type]
            session=session,
            tool=tool,
            upstream=upstream,
            api_key=api_key,
            user_id=user.id,
            request_id="req-invalid-json",
        )
    )

    assert response.status_code == 502
    assert session.scalar(select(ExternalResource)) is None
    recorded = session.get(GatewayRequest, "req-invalid-json")
    assert recorded is not None and recorded.error_code == "UPSTREAM_INVALID_JSON"


def test_proxy_fails_closed_when_resource_response_is_not_json(session: Session) -> None:
    tool, upstream, user, api_key = seed_gateway(session, method="POST", path="/api/v1/datasets", content_types=["application/json"])
    FakeAsyncClient.response_factory = lambda request: httpx.Response(201, text="created", headers={"content-type": "text/plain"})

    response = asyncio.run(
        proxy_gateway_request(
            request=RequestLike(method="POST", path="/api/v1/datasets", headers={"content-type": "application/json"}, body=b'{"name":"dataset"}'),  # type: ignore[arg-type]
            session=session,
            tool=tool,
            upstream=upstream,
            api_key=api_key,
            user_id=user.id,
            request_id="req-non-json-resource",
        )
    )

    assert response.status_code == 502
    assert json.loads(response.body)["code"] == "RESOURCE_POLICY_FAILED"
    assert session.scalar(select(ExternalResource)) is None
    recorded = session.get(GatewayRequest, "req-non-json-resource")
    assert recorded is not None and recorded.error_code == "RESOURCE_POLICY_FAILED"


def test_proxy_strips_client_credentials_and_injects_machine_credential(session: Session) -> None:
    tool, upstream, user, api_key = seed_gateway(session, method="GET", path="/health")
    upstream.auth_type = "bearer"
    upstream.token_ciphertext = encrypt_secret("machine-token")

    def upstream_response(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer machine-token"
        assert "cookie" not in request.headers
        assert "x-api-key" not in request.headers
        return httpx.Response(200, json={"status": "ok"})

    FakeAsyncClient.response_factory = upstream_response
    response = asyncio.run(
        proxy_gateway_request(
            request=RequestLike(
                method="GET",
                path="/health",
                headers={"authorization": "Bearer client-token", "cookie": "session=client", "x-api-key": "agw-client"},
            ),  # type: ignore[arg-type]
            session=session,
            tool=tool,
            upstream=upstream,
            api_key=api_key,
            user_id=user.id,
            request_id="req-sensitive-headers",
        )
    )
    assert response.status_code == 200


def test_proxy_timeout_maps_to_504_and_records_failure(session: Session) -> None:
    tool, upstream, user, api_key = seed_gateway(session, method="GET", path="/api/v1/jobs/{job_id}")
    session.add(ExternalResource(tool_id=tool.id, owner_user_id=user.id, kind=ResourceKind.JOB.value, upstream_id="job-timeout"))
    session.flush()
    FakeAsyncClient.raised_error = httpx.TimeoutException("timeout")

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            proxy_gateway_request(
                request=RequestLike(method="GET", path="/api/v1/jobs/job-timeout"),  # type: ignore[arg-type]
                session=session,
                tool=tool,
                upstream=upstream,
                api_key=api_key,
                user_id=user.id,
                request_id="req-timeout",
            )
        )

    assert error.value.status_code == 504
    recorded = session.get(GatewayRequest, "req-timeout")
    assert recorded is not None and recorded.error_code == "UPSTREAM_TIMEOUT"


def test_proxy_network_failure_marks_instance_unavailable_and_records_it(session: Session) -> None:
    tool, upstream, user, api_key = seed_gateway(session, method="GET", path="/api/v1/jobs/{job_id}")
    instance = ToolUpstreamInstance(
        tool_id=tool.id,
        upstream_config_id=upstream.id,
        environment_name="prod",
        instance_name="primary",
        base_url="http://127.0.0.1:18000",
        priority=10,
        is_enabled=True,
    )
    session.add_all([ExternalResource(tool_id=tool.id, owner_user_id=user.id, kind=ResourceKind.JOB.value, upstream_id="job-connect"), instance])
    session.flush()
    FakeAsyncClient.raised_error = httpx.ConnectError("connect failed")

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            proxy_gateway_request(
                request=RequestLike(method="GET", path="/api/v1/jobs/job-connect"),  # type: ignore[arg-type]
                session=session,
                tool=tool,
                upstream=upstream,
                api_key=api_key,
                user_id=user.id,
                request_id="req-connect",
                upstream_base_url=instance.base_url,
                upstream_instance=instance,
            )
        )

    assert error.value.status_code == 502
    assert instance.health_status == "unavailable"
    assert instance.failure_count == 1
    assert instance.last_error == "UPSTREAM_UNAVAILABLE"
    recorded = session.get(GatewayRequest, "req-connect")
    assert recorded is not None
    assert recorded.upstream_instance_id == instance.id
    assert recorded.upstream_environment == "prod"
    assert recorded.upstream_instance_name == "primary"


def test_proxy_success_resets_instance_health(session: Session) -> None:
    tool, upstream, user, api_key = seed_gateway(session, method="GET", path="/api/v1/jobs/{job_id}")
    session.add(ExternalResource(tool_id=tool.id, owner_user_id=user.id, kind=ResourceKind.JOB.value, upstream_id="job-ok"))
    instance = ToolUpstreamInstance(
        tool_id=tool.id,
        upstream_config_id=upstream.id,
        environment_name="prod",
        instance_name="recovering",
        base_url="http://127.0.0.1:18000",
        priority=10,
        is_enabled=True,
        health_status="degraded",
        failure_count=2,
        last_error="UPSTREAM_500",
    )
    session.add(instance)
    session.flush()
    FakeAsyncClient.response_factory = lambda request: httpx.Response(200, json={"job_id": "job-ok", "status": "succeeded"})

    response = asyncio.run(
        proxy_gateway_request(
            request=RequestLike(method="GET", path="/api/v1/jobs/job-ok"),  # type: ignore[arg-type]
            session=session,
            tool=tool,
            upstream=upstream,
            api_key=api_key,
            user_id=user.id,
            request_id="req-recover",
            upstream_base_url=instance.base_url,
            upstream_instance=instance,
        )
    )

    assert response.status_code == 200
    assert instance.health_status == "ok"
    assert instance.failure_count == 0
    assert instance.last_error == ""
    recorded = session.get(GatewayRequest, "req-recover")
    assert recorded is not None and recorded.upstream_instance_name == "recovering"


def test_proxy_declared_upload_too_large_is_recorded(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway_proxy.get_settings(), "max_upload_bytes", 8)
    tool, upstream, user, api_key = seed_gateway(session, method="POST", path="/api/v1/datasets", content_types=["application/json"])

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            proxy_gateway_request(
                request=RequestLike(method="POST", path="/api/v1/datasets", headers={"content-type": "application/json", "content-length": "9"}, body=b'{"x":1234}'),  # type: ignore[arg-type]
                session=session,
                tool=tool,
                upstream=upstream,
                api_key=api_key,
                user_id=user.id,
                request_id="req-upload-large",
            )
        )

    assert error.value.status_code == 413
    recorded = session.get(GatewayRequest, "req-upload-large")
    assert recorded is not None and recorded.error_code == "PAYLOAD_TOO_LARGE"


def test_proxy_uses_route_specific_upload_limit(session: Session) -> None:
    tool, upstream, user, api_key = seed_gateway(session, method="POST", path="/api/v1/datasets", content_types=["application/json"])
    route = session.scalar(select(PublishedRoute).where(PublishedRoute.tool_id == tool.id))
    assert route is not None
    route.max_request_bytes = 8
    session.flush()

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            proxy_gateway_request(
                request=RequestLike(method="POST", path="/api/v1/datasets", headers={"content-type": "application/json", "content-length": "9"}, body=b'{"x":1234}'),  # type: ignore[arg-type]
                session=session,
                tool=tool,
                upstream=upstream,
                api_key=api_key,
                user_id=user.id,
                request_id="req-route-upload-large",
            )
        )

    assert error.value.status_code == 413
    recorded = session.get(GatewayRequest, "req-route-upload-large")
    assert recorded is not None and recorded.error_code == "PAYLOAD_TOO_LARGE"


def test_proxy_enforces_route_idempotency_policy(session: Session) -> None:
    tool, upstream, user, api_key = seed_gateway(session, method="POST", path="/api/v1/datasets", content_types=["application/json"])
    route = session.scalar(select(PublishedRoute).where(PublishedRoute.tool_id == tool.id))
    assert route is not None
    route.require_idempotency_key = True
    session.flush()

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            proxy_gateway_request(
                request=RequestLike(method="POST", path="/api/v1/datasets", headers={"content-type": "application/json"}, body=b'{"name":"demo"}'),  # type: ignore[arg-type]
                session=session,
                tool=tool,
                upstream=upstream,
                api_key=api_key,
                user_id=user.id,
                request_id="req-idempotency",
            )
        )

    assert error.value.status_code == 422
    recorded = session.get(GatewayRequest, "req-idempotency")
    assert recorded is not None and recorded.error_code == "VALIDATION_ERROR"


def test_proxy_reuses_idempotent_result_and_forwards_key_once(session: Session) -> None:
    tool, upstream, user, api_key = seed_gateway(session, method="POST", path="/api/v1/datasets", content_types=["application/json"])
    route = session.scalar(select(PublishedRoute).where(PublishedRoute.tool_id == tool.id))
    assert route is not None
    route.require_idempotency_key = True
    session.flush()
    calls = 0

    def upstream_response(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["idempotency-key"] == "create-1"
        return httpx.Response(201, json={"dataset_id": "dataset-1"})

    FakeAsyncClient.response_factory = upstream_response
    request = RequestLike(method="POST", path="/api/v1/datasets", headers={"content-type": "application/json", "idempotency-key": "create-1"}, body=b'{"name":"demo"}')
    first = asyncio.run(proxy_gateway_request(request=request, session=session, tool=tool, upstream=upstream, api_key=api_key, user_id=user.id, request_id="req-idem-first"))
    second = asyncio.run(proxy_gateway_request(request=RequestLike(method="POST", path="/api/v1/datasets", headers={"content-type": "application/json", "idempotency-key": "create-1"}, body=b'{"name":"demo"}'), session=session, tool=tool, upstream=upstream, api_key=api_key, user_id=user.id, request_id="req-idem-second"))
    assert first.status_code == second.status_code == 201
    assert calls == 1
    assert session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.published_route_id == route.id)).status == "completed"  # type: ignore[union-attr]


def test_幂等键重放上游失败结果且不重复调用(session: Session) -> None:
    """可确定的上游失败结果应重放，避免客户端重试制造第二次副作用。"""
    tool, upstream, user, api_key = seed_gateway(session, method="POST", path="/api/v1/datasets", content_types=["application/json"])
    route = session.scalar(select(PublishedRoute).where(PublishedRoute.tool_id == tool.id))
    assert route is not None
    route.require_idempotency_key = True
    session.flush()
    calls = 0

    def upstream_response(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"internal": "不应泄露"})

    FakeAsyncClient.response_factory = upstream_response
    request_headers = {"content-type": "application/json", "idempotency-key": "failure-replay"}
    first = asyncio.run(
        proxy_gateway_request(
            request=RequestLike(method="POST", path="/api/v1/datasets", headers=request_headers, body=b'{"name":"demo"}'),
            session=session,
            tool=tool,
            upstream=upstream,
            api_key=api_key,
            user_id=user.id,
            request_id="req-idempotency-failure-first",
        )
    )
    second = asyncio.run(
        proxy_gateway_request(
            request=RequestLike(method="POST", path="/api/v1/datasets", headers=request_headers, body=b'{"name":"demo"}'),
            session=session,
            tool=tool,
            upstream=upstream,
            api_key=api_key,
            user_id=user.id,
            request_id="req-idempotency-failure-second",
        )
    )

    assert first.status_code == second.status_code == 503
    assert json.loads(first.body)["code"] == json.loads(second.body)["code"] == "UPSTREAM_ERROR"
    assert calls == 1
    records = session.scalars(select(IdempotencyRecord).where(IdempotencyRecord.published_route_id == route.id)).all()
    assert len(records) == 1 and records[0].status == "completed"
    assert len(session.scalars(select(GatewayRequest).where(GatewayRequest.published_route_id == route.id)).all()) == 1


def test_idempotency_key_rejects_different_request_body(session: Session) -> None:
    tool, upstream, user, api_key = seed_gateway(session, method="POST", path="/api/v1/datasets", content_types=["application/json"])
    route = session.scalar(select(PublishedRoute).where(PublishedRoute.tool_id == tool.id))
    assert route is not None
    route.require_idempotency_key = True
    session.flush()
    FakeAsyncClient.response_factory = lambda request: httpx.Response(201, json={"ok": True})
    request_args = {"session": session, "tool": tool, "upstream": upstream, "api_key": api_key, "user_id": user.id}
    asyncio.run(proxy_gateway_request(request=RequestLike(method="POST", path="/api/v1/datasets", headers={"content-type": "application/json", "idempotency-key": "same-key"}, body=b'{"name":"a"}'), request_id="req-idem-body-a", **request_args))
    with pytest.raises(HTTPException) as error:
        asyncio.run(proxy_gateway_request(request=RequestLike(method="POST", path="/api/v1/datasets", headers={"content-type": "application/json", "idempotency-key": "same-key"}, body=b'{"name":"b"}'), request_id="req-idem-body-b", **request_args))
    assert error.value.status_code == 422


def test_过期幂等键允许新的首次调用(session: Session) -> None:
    """过期记录必须被替换，不能让后续独立调用永久冲突。"""
    tool, _, user, api_key = seed_gateway(session, method="POST", path="/api/v1/datasets", content_types=["application/json"])
    route = session.scalar(select(PublishedRoute).where(PublishedRoute.tool_id == tool.id))
    assert route is not None
    expired = IdempotencyRecord(
        user_id=user.id,
        api_key_id=api_key.id,
        published_route_id=route.id,
        key_hash=hash_idempotency_key("expired-key"),
        request_fingerprint="old-fingerprint",
        status="completed",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    session.add(expired)
    session.flush()

    claim = claim_idempotency_record(
        session,
        user_id=user.id,
        api_key=api_key,
        route=route,
        raw_key="expired-key",
        fingerprint="new-fingerprint",
    )

    assert claim.owner is True
    assert claim.record.id != expired.id
    assert claim.record.status == "processing"
    assert session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.id == expired.id)) is None


def test_proxy_blocks_admin_only_route_for_normal_user(session: Session) -> None:
    tool, upstream, user, api_key = seed_gateway(session, method="GET", path="/api/v1/jobs/{job_id}")
    tool.slug = "generic"
    route = session.scalar(select(PublishedRoute).where(PublishedRoute.tool_id == tool.id))
    assert route is not None
    route.access_mode = "admin_only"
    session.flush()

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            proxy_gateway_request(
                request=RequestLike(method="GET", path="/api/v1/jobs/job-1"),  # type: ignore[arg-type]
                session=session,
                tool=tool,
                upstream=upstream,
                api_key=api_key,
                user_id=user.id,
                request_id="req-admin-required",
            )
        )

    assert error.value.status_code == 403
    recorded = session.get(GatewayRequest, "req-admin-required")
    assert recorded is not None and recorded.error_code == "FORBIDDEN"


def test_proxy_buffers_small_non_json_download_when_streaming_is_disabled(session: Session) -> None:
    tool, upstream, user, api_key = seed_gateway(session, method="GET", path="/api/v1/readme")
    route = session.scalar(select(PublishedRoute).where(PublishedRoute.tool_id == tool.id))
    assert route is not None
    route.allow_stream_download = False
    route.max_response_bytes = 64
    session.flush()
    FakeAsyncClient.response_factory = lambda request: httpx.Response(200, content=b"plain text", headers={"content-type": "text/plain"})

    response = asyncio.run(
        proxy_gateway_request(
            request=RequestLike(method="GET", path="/api/v1/readme"),  # type: ignore[arg-type]
            session=session,
            tool=tool,
            upstream=upstream,
            api_key=api_key,
            user_id=user.id,
            request_id="req-non-stream-text",
        )
    )

    assert response.status_code == 200
    assert response.body == b"plain text"
    recorded = session.get(GatewayRequest, "req-non-stream-text")
    assert recorded is not None and recorded.response_bytes == len(b"plain text")


def test_download_rejects_oversized_content_length(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(portal.get_settings(), "max_download_bytes", 8)
    upstream = ToolUpstream(tool_id="tool-1", base_url="http://127.0.0.1:18000", token_ciphertext="")
    FakeAsyncClient.response_factory = lambda request: httpx.Response(200, content=b"x" * 16, headers={"content-length": "16", "content-type": "application/zip"})

    response = asyncio.run(portal.stream_tomodd_download(upstream, "/api/v1/jobs/job-1/download.zip", "req-download-large"))

    assert response.status_code == 413
    assert json.loads(response.body)["code"] == "UPSTREAM_RESPONSE_TOO_LARGE"


def test_download_sanitizes_filename_and_strips_redirect_location() -> None:
    upstream = ToolUpstream(tool_id="tool-1", base_url="http://127.0.0.1:18000", token_ciphertext="")
    FakeAsyncClient.response_factory = lambda request: httpx.Response(
        200,
        content=b"zip",
        headers={
            "content-type": "application/zip",
            "content-disposition": 'attachment; filename="../secret\\result.zip\r\nX-Evil: 1"',
            "location": "http://127.0.0.1:18000/private",
        },
    )

    response = asyncio.run(portal.stream_tomodd_download(upstream, "/api/v1/jobs/job-1/download.zip", "req-download-safe"))

    assert response.status_code == 200
    assert "location" not in response.headers
    assert ".." not in response.headers["content-disposition"]
    assert "X-Evil" not in response.headers["content-disposition"]

