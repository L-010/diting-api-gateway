from __future__ import annotations

import csv
import io

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import ApiEndpoint, ApiKey, ApprovalStatus, ExternalResource, GatewayRequest, KeyStatus, PublishedRoute, ResourceKind, Tool, User
from app.routers.admin import BoundedHashingReader
from app.routers.gateway import api_key_allows_tool, authenticate_api_key
from app.routers.portal import get_owned_job
from app.services.rate_limit import consume_fixed_window, consume_user_fixed_window
from app.services.gateway_proxy import (
    ensure_content_type_allowed,
    ensure_declared_upload_size,
    get_published_route,
    raise_gateway_failure,
    render_upstream_path,
    route_match_params,
    safe_download_disposition,
    validate_upstream_url,
)
from app.services.resource_policies import execute_post_actions, execute_pre_checks, filter_owned_list
from app.services.security import hash_api_key


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as current:
        yield current


def test_stream_csv_reader_hashes_without_retaining_source() -> None:
    source = io.BytesIO(b"username,password,role\nalice,AaPass2026!x,user\n")
    stream = BoundedHashingReader(source, 1024)
    text = io.TextIOWrapper(io.BufferedReader(stream), encoding="utf-8-sig", newline="")
    rows = list(csv.DictReader(text))
    assert rows[0]["username"] == "alice"
    assert len(stream.digest.hexdigest()) == 64


def test_stream_csv_reader_rejects_oversize_input() -> None:
    stream = BoundedHashingReader(io.BytesIO(b"x" * 9), 8)
    with pytest.raises(Exception):
        io.BufferedReader(stream).read()


def test_upstream_validation_rejects_non_whitelisted_host() -> None:
    assert validate_upstream_url("http://127.0.0.1:18000") == "http://127.0.0.1:18000"
    with pytest.raises(ValueError):
        validate_upstream_url("https://example.com")


def test_route_template_extracts_and_renders_path_params() -> None:
    params = route_match_params("/api/v1/jobs/{job_id}/manifest", "/api/v1/jobs/job-123/manifest")
    assert params == {"job_id": "job-123"}
    assert render_upstream_path("/api/v1/jobs/{job_id}/manifest", params or {}) == "/api/v1/jobs/job-123/manifest"
    assert route_match_params("/api/v1/jobs/{job_id}", "/api/v1/jobs/job-123/manifest") is None


def test_静态路由优先于参数路由(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """静态路径与参数路径重叠时，必须优先匹配静态路径。"""
    tool = Tool(slug="route-order", name="Route Order")
    user = User(username="route-order-user", password_hash="hash")
    session.add_all([tool, user])
    session.flush()
    parameter_endpoint = ApiEndpoint(tool_id=tool.id, method="GET", upstream_path="/api/items/{item_id}", status="published")
    static_endpoint = ApiEndpoint(tool_id=tool.id, method="GET", upstream_path="/api/items/special", status="published")
    session.add_all([parameter_endpoint, static_endpoint])
    session.flush()
    # 故意先插入参数路由，复现未排序查询可能产生的错误匹配。
    parameter_route = PublishedRoute(tool_id=tool.id, endpoint_id=parameter_endpoint.id, method="GET", gateway_path="/api/items/{item_id}", upstream_path="/api/items/{item_id}")
    static_route = PublishedRoute(tool_id=tool.id, endpoint_id=static_endpoint.id, method="GET", gateway_path="/api/items/special", upstream_path="/api/items/special")
    session.add_all([parameter_route, static_route])
    session.flush()

    # 模拟数据库未承诺的任意返回顺序，确保测试验证匹配算法而非 SQLite 偶然顺序。
    original_scalars = session.scalars

    def reversed_scalars(statement):
        result = original_scalars(statement)
        values = result.all()
        return type("ScalarResultStub", (), {"all": lambda self: list(reversed(values))})()

    monkeypatch.setattr(session, "scalars", reversed_scalars)

    matched = get_published_route(session, tool.id, "GET", "/api/items/special")

    assert matched is not None
    route, params = matched
    assert route.id == static_route.id
    assert params == {}


def test_content_type_is_limited_by_imported_endpoint(session: Session) -> None:
    tool = Tool(slug="tomodd", name="tomoDD")
    session.add(tool)
    session.flush()
    endpoint = ApiEndpoint(tool_id=tool.id, method="POST", upstream_path="/api/v1/jobs/pipeline", content_types='["application/json"]')
    session.add(endpoint)
    session.flush()
    route = PublishedRoute(
        tool_id=tool.id,
        endpoint_id=endpoint.id,
        method="POST",
        gateway_path="/api/v1/jobs/pipeline",
        upstream_path="/api/v1/jobs/pipeline",
    )
    session.add(route)
    session.flush()

    class RequestLike:
        headers = {"content-type": "multipart/form-data; boundary=x"}

    with pytest.raises(HTTPException) as error:
        ensure_content_type_allowed(session, route, RequestLike())  # type: ignore[arg-type]
    assert error.value.status_code == 415


def test_负数内容长度被拒绝(session: Session) -> None:
    """错误的负数 Content-Length 不得绕过 Gateway 的请求体边界校验。"""
    tool = Tool(slug="negative-length", name="Negative Length")
    session.add(tool)
    session.flush()
    endpoint = ApiEndpoint(tool_id=tool.id, method="POST", upstream_path="/api/v1/items")
    session.add(endpoint)
    session.flush()
    route = PublishedRoute(
        tool_id=tool.id,
        endpoint_id=endpoint.id,
        method="POST",
        gateway_path="/api/v1/items",
        upstream_path="/api/v1/items",
    )
    session.add(route)
    session.flush()

    class RequestLike:
        headers = {"content-length": "-1"}

    with pytest.raises(HTTPException) as error:
        ensure_declared_upload_size(RequestLike(), route)  # type: ignore[arg-type]

    assert error.value.status_code == 400


def test_resource_owner_and_global_list_boundaries(session: Session) -> None:
    owner = User(username="owner", password_hash="hash")
    another = User(username="another", password_hash="hash")
    tool = Tool(slug="tomodd", name="tomoDD")
    session.add_all([owner, another, tool])
    session.flush()
    endpoint = ApiEndpoint(tool_id=tool.id, method="GET", upstream_path="/api/v1/datasets/{dataset_id}")
    session.add(endpoint)
    session.flush()
    route = PublishedRoute(tool_id=tool.id, endpoint_id=endpoint.id, method="GET", gateway_path=endpoint.upstream_path, upstream_path=endpoint.upstream_path, access_mode="owner", resource_policy_json='{"version":1,"pre_checks":[{"action":"require_owner","resource_kind":"dataset","source":"path","selector":"dataset_id"}],"post_actions":[],"list_filter":null}')
    session.add_all([route, ExternalResource(tool_id=tool.id, owner_user_id=owner.id, kind=ResourceKind.DATASET.value, upstream_id="dataset-1")])
    session.flush()
    execute_pre_checks(session, route=route, user_id=owner.id, path_params={"dataset_id": "dataset-1"}, query_params={}, request_json={})
    with pytest.raises(HTTPException, match="资源不存在"):
        execute_pre_checks(session, route=route, user_id=another.id, path_params={"dataset_id": "dataset-1"}, query_params={}, request_json={})


def test_owned_list_filter_returns_only_current_user_resources(session: Session) -> None:
    owner = User(username="list-owner", password_hash="hash")
    another = User(username="list-another", password_hash="hash")
    tool = Tool(slug="list-tool", name="List Tool")
    session.add_all([owner, another, tool])
    session.flush()
    endpoint = ApiEndpoint(tool_id=tool.id, method="GET", upstream_path="/api/v1/datasets")
    session.add(endpoint)
    session.flush()
    route = PublishedRoute(
        tool_id=tool.id,
        endpoint_id=endpoint.id,
        method="GET",
        gateway_path=endpoint.upstream_path,
        upstream_path=endpoint.upstream_path,
        access_mode="owner",
        resource_policy_json='{"version":1,"pre_checks":[],"post_actions":[],"list_filter":{"items_selectors":["$","$.items"],"id_selectors":["$.dataset_id","$.id"],"resource_kind":"dataset"}}',
    )
    session.add_all(
        [
            route,
            ExternalResource(tool_id=tool.id, owner_user_id=owner.id, kind="dataset", upstream_id="dataset-a"),
            ExternalResource(tool_id=tool.id, owner_user_id=another.id, kind="dataset", upstream_id="dataset-b"),
        ]
    )
    session.flush()

    filtered = filter_owned_list(
        session,
        route=route,
        user_id=owner.id,
        payload={"items": [{"dataset_id": "dataset-a"}, {"id": "dataset-b"}]},
    )
    assert filtered == {"items": [{"dataset_id": "dataset-a"}]}


def test_job_creation_rejects_foreign_dataset_reference(session: Session) -> None:
    owner = User(username="owner", password_hash="hash")
    another = User(username="another", password_hash="hash")
    tool = Tool(slug="tomodd", name="tomoDD")
    session.add_all([owner, another, tool])
    session.flush()
    endpoint = ApiEndpoint(tool_id=tool.id, method="POST", upstream_path="/api/v1/jobs/phase-selection")
    session.add(endpoint)
    session.flush()
    route = PublishedRoute(tool_id=tool.id, endpoint_id=endpoint.id, method="POST", gateway_path=endpoint.upstream_path, upstream_path=endpoint.upstream_path, access_mode="owner", resource_policy_json='{"version":1,"pre_checks":[{"action":"require_parent_owner","resource_kind":"dataset","source":"request_json","selector":"$.dataset_id"}],"post_actions":[],"list_filter":null}')
    session.add_all([route, ExternalResource(tool_id=tool.id, owner_user_id=owner.id, kind=ResourceKind.DATASET.value, upstream_id="dataset-1")])
    session.flush()
    execute_pre_checks(session, route=route, user_id=owner.id, path_params={}, query_params={}, request_json={"dataset_id": "dataset-1"})
    with pytest.raises(HTTPException) as error:
        execute_pre_checks(session, route=route, user_id=another.id, path_params={}, query_params={}, request_json={"dataset_id": "dataset-1"})
    assert error.value.status_code == 404


def test_register_response_resources_maps_job_artifacts(session: Session) -> None:
    owner = User(username="owner", password_hash="hash")
    tool = Tool(slug="tomodd", name="tomoDD")
    session.add_all([owner, tool])
    session.flush()
    endpoint = ApiEndpoint(tool_id=tool.id, method="GET", upstream_path="/api/v1/jobs/{job_id}/artifacts")
    session.add(endpoint)
    session.flush()
    route = PublishedRoute(tool_id=tool.id, endpoint_id=endpoint.id, method="GET", gateway_path=endpoint.upstream_path, upstream_path=endpoint.upstream_path, access_mode="owner", resource_policy_json='{"version":1,"pre_checks":[],"post_actions":[{"action":"register_owner","resource_kind":"job","source":"path","selector":"job_id"},{"action":"register_owner","resource_kind":"artifact","source":"response_json","selector":"$..artifact_id","many":true,"parent":{"source":"path","selector":"job_id"}}],"list_filter":null}')
    session.add(route)
    session.flush()
    execute_post_actions(session, route=route, user_id=owner.id, path_params={"job_id": "job-1"}, query_params={}, request_json={}, response_json={"status": "succeeded", "artifacts": [{"artifact_id": "artifact-1", "name": "result.zip"}]}, response_headers={})
    session.flush()
    resources = session.query(ExternalResource).all()
    assert {(item.kind, item.upstream_id, item.parent_upstream_id) for item in resources} == {
        (ResourceKind.JOB.value, "job-1", None),
        (ResourceKind.ARTIFACT.value, "artifact-1", "job-1"),
    }


def test_task_control_plane_rejects_foreign_job(session: Session) -> None:
    owner = User(username="owner", password_hash="hash")
    another = User(username="another", password_hash="hash")
    tool = Tool(slug="tomodd", name="tomoDD")
    session.add_all([owner, another, tool])
    session.flush()
    session.add(ExternalResource(tool_id=tool.id, owner_user_id=owner.id, kind=ResourceKind.JOB.value, upstream_id="job-1"))
    session.flush()
    assert get_owned_job(session, tool.id, owner.id, "job-1").upstream_id == "job-1"
    with pytest.raises(HTTPException) as error:
        get_owned_job(session, tool.id, another.id, "job-1")
    assert error.value.status_code == 404


def test_fixed_window_limiter_blocks_limit_plus_one(session: Session) -> None:
    consume_fixed_window(session, "key-1", "tool-1", 1)
    consume_fixed_window(session, "key-1", "tool-2", 1)
    with pytest.raises(HTTPException) as error:
        consume_fixed_window(session, "key-1", "tool-1", 1)
    assert error.value.status_code == 429


def test_user_fixed_window_limiter_aggregates_and_zero_is_unlimited(session: Session) -> None:
    consume_user_fixed_window(session, "user-1", 1)
    with pytest.raises(HTTPException) as error:
        consume_user_fixed_window(session, "user-1", 1)
    assert error.value.status_code == 429
    consume_user_fixed_window(session, "user-unlimited", 0)
    consume_user_fixed_window(session, "user-unlimited", 0)


def test_download_filename_is_normalized() -> None:
    header = safe_download_disposition('attachment; filename="../unsafe\\result.zip\r\nX-Evil: 1"')
    assert "\r" not in header and "\n" not in header
    assert ".." not in header and "X-Evil" not in header
    assert header.startswith("attachment; filename*=UTF-8''")


def test_gateway_failure_is_recorded(session: Session) -> None:
    with pytest.raises(HTTPException):
        raise_gateway_failure(
            session,
            request_id="request-1",
            user_id="user-1",
            api_key_id="key-1",
            tool_id="tool-1",
            method="POST",
            path="/api/v1/jobs/pipeline",
            started=0,
            status_code=415,
            detail="Content-Type 不受支持",
        )
    row = session.get(GatewayRequest, "request-1")
    assert row is not None
    assert row.status_code == 415
    assert row.error_code == "UNSUPPORTED_MEDIA_TYPE"


def test_api_key_scope_supports_dynamic_tool_and_legacy_tomodd() -> None:
    key = ApiKey(user_id="user-1", label="scope", prefix="agw", secret_hash="hash", scopes="tool:demo:*,tomodd:*")
    assert api_key_allows_tool(key, "demo") is True
    assert api_key_allows_tool(key, "tomodd") is True
    assert api_key_allows_tool(key, "other") is False
    global_key = ApiKey(user_id="user-1", label="global", prefix="agw", secret_hash="hash-global", scopes="*")
    assert api_key_allows_tool(global_key, "demo") is True
    assert api_key_allows_tool(global_key, "tomodd") is True
    assert api_key_allows_tool(global_key, "other") is True


def test_api_key_auth_requires_approved_active_user(session: Session) -> None:
    secret = "agw_test_secret_for_pending_user"
    user = User(username="pending", password_hash="hash", is_active=False, approval_status=ApprovalStatus.PENDING.value, must_change_password=False)
    session.add(user)
    session.flush()
    key = ApiKey(user_id=user.id, label="pending", prefix=secret[:16], secret_hash=hash_api_key(secret), scopes="*", status=KeyStatus.ACTIVE.value)
    session.add(key)
    session.commit()
    with pytest.raises(HTTPException) as error:
        authenticate_api_key(session, secret)
    assert error.value.status_code == 401

    user.approval_status = ApprovalStatus.APPROVED.value
    user.is_active = True
    session.commit()
    authenticated_key, authenticated_user = authenticate_api_key(session, secret)
    assert authenticated_key.id == key.id
    assert authenticated_user.id == user.id
