from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app import file_worker
from app.database import Base
from app.models import ExternalResource, FileDownloadAuthorization, FileDownloadEvent, FileDownloadSlot, FileSyncJob, FileWorkerHeartbeat, RemoteFile, Tool, ToolUpstream, User
from app.routers.files import _reserve_download_event
from app.routers.portal import get_my_task
from app.services.remote_files import (
    _manifest_mapping,
    bind_resource_storage_endpoint,
    complete_sync_job,
    effective_user_quota,
    ensure_storage_endpoint,
    ensure_write_capacity,
    fail_sync_job,
    render_capability_path,
    sanitize_file_name,
    upsert_manifest_file,
    utc_now,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as current:
        yield current


def _context(session: Session) -> tuple[Tool, ToolUpstream, User]:
    user = User(username="file-owner", password_hash="hash", must_change_password=False, storage_quota_bytes=1_000)
    tool = Tool(slug="files", name="文件工具", storage_risk_acknowledged=True)
    session.add_all([user, tool])
    session.flush()
    upstream = ToolUpstream(tool_id=tool.id, base_url="http://127.0.0.1:18000", auth_type="none")
    session.add(upstream)
    session.flush()
    return tool, upstream, user


def test_manifest_defaults_fail_closed_and_file_name_is_sanitized(session: Session) -> None:
    tool, upstream, user = _context(session)
    endpoint = ensure_storage_endpoint(session, tool, upstream)
    mapping = _manifest_mapping({"artifacts": {"items_selector": "$", "id_selector": "$.artifact_id", "name_selector": "$.name", "size_selector": "$.size_bytes"}})
    item, created = upsert_manifest_file(
        session,
        tool=tool,
        owner_user_id=user.id,
        endpoint=endpoint,
        raw={"artifact_id": "stable-id", "name": "../../结果\r\n.csv"},
        mapping=mapping,
        parent_resource=None,
        request_id=None,
    )
    assert created is True
    assert item.file_name == "结果.csv"
    assert item.visibility == "internal"
    assert item.status == "pending"
    assert item.size_bytes is None
    assert sanitize_file_name("..\\..\\report.csv\nSet-Cookie:x") == "report.csvSet-Cookie:x"


def test_ready_manifest_is_idempotent_and_owner_conflict_is_quarantined(session: Session) -> None:
    tool, upstream, owner = _context(session)
    other = User(username="other-owner", password_hash="hash", must_change_password=False)
    session.add(other)
    session.flush()
    endpoint = ensure_storage_endpoint(session, tool, upstream)
    mapping = _manifest_mapping({"artifacts": {"id_selector": "$.artifact_id", "name_selector": "$.name", "size_selector": "$.size_bytes", "default_visibility": "user", "default_role": "output"}})
    raw = {"artifact_id": "same-id", "name": "result.bin", "size_bytes": 20}
    first, created = upsert_manifest_file(session, tool=tool, owner_user_id=owner.id, endpoint=endpoint, raw=raw, mapping=mapping, parent_resource=None, request_id=None)
    second, created_again = upsert_manifest_file(session, tool=tool, owner_user_id=owner.id, endpoint=endpoint, raw=raw, mapping=mapping, parent_resource=None, request_id=None)
    assert first.id == second.id
    assert created is True and created_again is False
    assert first.status == "ready" and first.visibility == "user" and first.role == "output"
    with pytest.raises(HTTPException) as conflict:
        upsert_manifest_file(session, tool=tool, owner_user_id=other.id, endpoint=endpoint, raw=raw, mapping=mapping, parent_resource=None, request_id=None)
    assert conflict.value.status_code == 502
    assert first.status == "quarantined"
    assert session.scalar(select(RemoteFile).where(RemoteFile.identity_key == first.identity_key)).owner_user_id == owner.id


def test_文件重新出现时清除删除墓碑(session: Session) -> None:
    """上游重新出现同一文件时，应恢复可管理状态而不是保留删除墓碑。"""
    tool, upstream, owner = _context(session)
    endpoint = ensure_storage_endpoint(session, tool, upstream)
    mapping = _manifest_mapping({"artifacts": {"id_selector": "$.id", "name_selector": "$.name", "size_selector": "$.size", "default_visibility": "user"}})
    item, _ = upsert_manifest_file(session, tool=tool, owner_user_id=owner.id, endpoint=endpoint, raw={"id": "resurrected", "name": "result.bin", "size": 20}, mapping=mapping, parent_resource=None, request_id=None)
    item.status = "deleted"
    item.deleted_at = utc_now() - timedelta(days=1)
    session.flush()

    restored, created = upsert_manifest_file(session, tool=tool, owner_user_id=owner.id, endpoint=endpoint, raw={"id": "resurrected", "name": "result.bin", "size": 20}, mapping=mapping, parent_resource=None, request_id=None)

    assert created is False
    assert restored.status == "ready"
    assert restored.deleted_at is None


def test_storage_endpoint_revision_is_immutable_after_server_change(session: Session) -> None:
    tool, upstream, _ = _context(session)
    first = ensure_storage_endpoint(session, tool, upstream)
    assert ensure_storage_endpoint(session, tool, upstream).id == first.id
    upstream.base_url = "http://127.0.0.1:18001"
    second = ensure_storage_endpoint(session, tool, upstream)
    assert second.id != first.id
    assert second.revision == 2
    assert first.is_active is False and first.retired_at is not None
    assert first.base_url == "http://127.0.0.1:18000"


def test_task_keeps_original_storage_endpoint_after_server_change(session: Session) -> None:
    tool, upstream, user = _context(session)
    resource = ExternalResource(tool_id=tool.id, owner_user_id=user.id, kind="job", upstream_id="job-1", status="running")
    session.add(resource)
    session.flush()
    first = bind_resource_storage_endpoint(session, resource)

    upstream.base_url = "http://127.0.0.1:18002"
    second = ensure_storage_endpoint(session, tool, upstream)

    assert second.id != first.id
    assert bind_resource_storage_endpoint(session, resource).id == first.id
    assert resource.storage_endpoint_revision_id == first.id


def test_user_task_detail_uses_platform_record_without_live_upstream(session: Session) -> None:
    tool, _, user = _context(session)
    resource = ExternalResource(tool_id=tool.id, owner_user_id=user.id, kind="job", upstream_id="missing-upstream-task", status="succeeded")
    session.add(resource)
    session.flush()
    bind_resource_storage_endpoint(session, resource)

    result = get_my_task(resource.id, user=user, session=session)

    assert result.id == resource.id
    assert result.status == "succeeded"
    assert result.upstream is None


def test_quota_expected_upload_and_storage_watermark_are_enforced(session: Session) -> None:
    tool, upstream, user = _context(session)
    assert effective_user_quota(session, user) == 1_000
    ensure_write_capacity(session, user, tool, expected_bytes=1_000)
    endpoint = ensure_storage_endpoint(session, tool, upstream)
    mapping = _manifest_mapping({"artifacts": {"id_selector": "$.id", "size_selector": "$.size", "default_visibility": "user"}})
    upsert_manifest_file(session, tool=tool, owner_user_id=user.id, endpoint=endpoint, raw={"id": "used", "size": 900}, mapping=mapping, parent_resource=None, request_id=None)
    with pytest.raises(HTTPException) as quota_error:
        ensure_write_capacity(session, user, tool, expected_bytes=101)
    assert quota_error.value.status_code == 413
    tool.storage_total_bytes = 10_000
    tool.storage_free_bytes = 499
    with pytest.raises(HTTPException) as watermark_error:
        ensure_write_capacity(session, user, tool)
    assert watermark_error.value.status_code == 503
    assert watermark_error.value.detail["code"] == "TOOL_STORAGE_READ_ONLY"


def test_stale_storage_watermark_fails_closed(session: Session) -> None:
    tool, _, user = _context(session)
    tool.storage_total_bytes = 10_000
    tool.storage_free_bytes = 9_000
    tool.storage_checked_at = utc_now() - timedelta(days=1)
    with pytest.raises(HTTPException) as stale_error:
        ensure_write_capacity(session, user, tool)
    assert stale_error.value.status_code == 503
    assert stale_error.value.detail["code"] == "TOOL_STORAGE_STATUS_STALE"


def test_terminal_sync_job_is_rescheduled_for_periodic_verification(session: Session) -> None:
    job = FileSyncJob(target_key="poll:terminal", job_type="poll", status="running", attempts=3, lease_token="lease", last_error="upstream failure")
    session.add(job)
    session.flush()
    before = utc_now()

    complete_sync_job(session, job, terminal=True)

    assert job.status == "pending"
    assert job.attempts == 0
    assert job.lease_token == ""
    assert job.last_error == ""
    assert job.next_attempt_at > before


def test_文件同步失败保留退避重试状态(session: Session) -> None:
    """Worker 失败后必须释放租约、记录错误并在后续时间重新尝试。"""
    job = FileSyncJob(target_key="verify:retry", job_type="verify", status="running", lease_token="lease")
    session.add(job)
    session.flush()
    before = utc_now()

    fail_sync_job(job, RuntimeError("模拟文件服务超时"))

    assert job.status == "retry"
    assert job.attempts == 1
    assert job.lease_token == ""
    assert job.lease_expires_at is None
    assert job.last_error == "FILE_SYNC_FAILED:RuntimeError"
    assert job.next_attempt_at > before


def test_download_slots_enforce_limit_and_reclaim_expired_lease(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    tool, upstream, user = _context(session)
    endpoint = ensure_storage_endpoint(session, tool, upstream)
    mapping = _manifest_mapping({"artifacts": {"id_selector": "$.id", "name_selector": "$.name", "size_selector": "$.size", "default_visibility": "user"}})
    item, _ = upsert_manifest_file(session, tool=tool, owner_user_id=user.id, endpoint=endpoint, raw={"id": "download", "name": "result.bin", "size": 20}, mapping=mapping, parent_resource=None, request_id=None)
    first_auth = FileDownloadAuthorization(remote_file_id=item.id, actor_user_id=user.id, is_admin=False, expires_at=utc_now() + timedelta(minutes=5))
    second_auth = FileDownloadAuthorization(remote_file_id=item.id, actor_user_id=user.id, is_admin=False, expires_at=utc_now() + timedelta(minutes=5))
    session.add_all([first_auth, second_auth])
    session.flush()
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "file_download_concurrency_per_user", 1)
    monkeypatch.setattr(settings, "file_download_concurrency_per_tool", 1)

    first = _reserve_download_event(session, authorization=first_auth, item=item, actor=user, request_value="request-1")
    with pytest.raises(HTTPException) as limited:
        _reserve_download_event(session, authorization=second_auth, item=item, actor=user, request_value="request-2")
    assert limited.value.status_code == 429

    for slot in session.scalars(select(FileDownloadSlot).where(FileDownloadSlot.event_id == first.id)).all():
        slot.expires_at = utc_now() - timedelta(seconds=1)
    session.commit()
    second = _reserve_download_event(session, authorization=second_auth, item=item, actor=user, request_value="request-3")
    session.refresh(first)
    assert second.status == "started"
    assert first.status == "interrupted"
    assert first.error_code == "DOWNLOAD_LEASE_EXPIRED"


def test_adapter_path_encodes_ids_and_rejects_incomplete_templates() -> None:
    assert render_capability_path("/jobs/{job_id}/files/{artifact_id}", job_id="a/b", file_id="x?y", file_id_param="artifact_id") == "/jobs/a%2Fb/files/x%3Fy"
    with pytest.raises(HTTPException):
        render_capability_path("/jobs/{unknown}/files/{file_id}", job_id="a", file_id="b")


def test_file_worker_heartbeat_accumulates_jobs_and_redacts_error(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(file_worker, "SessionLocal", factory)

    file_worker.record_heartbeat("worker-1", processed_jobs=2)
    file_worker.record_heartbeat("worker-1", processed_jobs=3, last_error="x" * 1_500)

    with Session(engine) as current:
        heartbeat = current.get(FileWorkerHeartbeat, "worker-1")
        assert heartbeat is not None
        assert heartbeat.processed_jobs == 5
        assert len(heartbeat.last_error) == 1_000
