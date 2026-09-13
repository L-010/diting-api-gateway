from __future__ import annotations

import multiprocessing
import os
import secrets
import sqlite3
import threading
import time
from datetime import timedelta
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sqlalchemy import create_engine


ROOT = Path(__file__).resolve().parents[2]


def _child_environment(database_url: str) -> None:
    os.environ["DATABASE_URL"] = database_url
    os.environ["APP_ENV"] = "test"
    os.environ["EMAIL_TEST_MODE"] = "true"
    os.environ["SMTP_HOST"] = "smtp.test.invalid"
    os.environ["SMTP_FROM"] = "gateway@example.test"
    os.environ["PYTHONPATH"] = str(ROOT / "backend")


def _register_in_process(database_url: str, username: str, email: str, password: str, barrier, results) -> None:
    _child_environment(database_url)
    from fastapi.testclient import TestClient
    from app.main import app

    barrier.wait(timeout=10)
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": password},
        )
    results.put((response.status_code, response.json().get("code"), response.json().get("message")))


def _claim_in_process(database_url: str, barrier, results) -> None:
    _child_environment(database_url)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.services.remote_files import claim_sync_jobs

    engine = create_engine(database_url, connect_args={"check_same_thread": False, "timeout": 2})
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    barrier.wait(timeout=10)
    try:
        with factory() as session:
            _, jobs = claim_sync_jobs(session, limit=1)
            results.put(("ok", [job.id for job in jobs]))
    except Exception as error:
        results.put(("error", type(error).__name__))
    finally:
        engine.dispose()


def _gateway_in_process(database_url: str, api_key: str, barrier, results) -> None:
    _child_environment(database_url)
    from fastapi.testclient import TestClient
    from app.main import app

    barrier.wait(timeout=10)
    with TestClient(app) as client:
        response = client.post(
            "/gateway/demo/echo",
            headers={"X-API-Key": api_key, "Idempotency-Key": "phase7-concurrent-key"},
            json={"request": "same"},
        )
    results.put((response.status_code, response.json().get("code")))


def _hold_exclusive_lock(database_path: str, ready) -> None:
    connection = sqlite3.connect(database_path, timeout=2, isolation_level=None)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        ready.set()
        time.sleep(0.8)
        connection.execute("ROLLBACK")
    finally:
        connection.close()


def _claim_and_crash(database_url: str, ready) -> None:
    _child_environment(database_url)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.services.remote_files import claim_sync_jobs

    engine = create_engine(database_url, connect_args={"check_same_thread": False, "timeout": 2})
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        _, jobs = claim_sync_jobs(session, limit=1)
        if jobs:
            ready.set()
    os._exit(17)


class _GatewayStub(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self) -> None:  # noqa: N802
        type(self).calls += 1
        body = b'{"ok":true}'
        self.send_response(201)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


def _run_processes(target, database_url: str, *arguments: tuple[object, ...]) -> list[tuple[object, ...]]:
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(len(arguments))
    results = context.Queue()
    workers = [context.Process(target=target, args=(database_url, *items, barrier, results)) for items in arguments]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
    assert all(worker.exitcode == 0 for worker in workers)
    values = [results.get(timeout=5) for _ in workers]
    return values


def test_multiprocess_sqlite_registration_and_claim_use_final_database_state(tmp_path) -> None:
    """独立进程只使用临时 SQLite，断言 HTTP 结果和任务最终租约均一致。"""

    database = tmp_path / "multiprocess.sqlite"
    database_url = f"sqlite:///{database.as_posix()}"
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from app.database import Base
    from app.models import FileSyncJob, PlatformSettings, Role, RoleName, User

    engine = create_engine(database_url, connect_args={"check_same_thread": False, "timeout": 2})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Role(name=RoleName.USER.value, description="普通用户"),
                Role(name=RoleName.ADMIN.value, description="管理员"),
                PlatformSettings(id=1, smtp_enabled=True, smtp_host="smtp.test.invalid", smtp_from_email="gateway@example.test"),
                FileSyncJob(target_key="poll:multiprocess", job_type="poll", status="pending"),
            ]
        )
        session.commit()

    password = f"Aa1{secrets.token_urlsafe(24)}"

    unique_results = _run_processes(
        _register_in_process,
        database_url,
        ("worker-one", "worker-one@example.test", password),
        ("worker-two", "worker-two@example.test", password),
    )
    assert sorted(result[0] for result in unique_results) == [201, 201], unique_results

    duplicate_results = _run_processes(
        _register_in_process,
        database_url,
        ("SameName", "same-one@example.test", password),
        (" samename ", "same-two@example.test", password),
    )
    assert sorted(result[0] for result in duplicate_results) == [201, 409], duplicate_results

    from app.models import ApiEndpoint, ApiKey, ApprovalStatus, IdempotencyRecord, KeyStatus, PublishedRoute, Tool, ToolStatus, ToolUpstream
    from app.services.security import hash_api_key

    stub = ThreadingHTTPServer(("127.0.0.1", 0), _GatewayStub)
    stub_thread = threading.Thread(target=stub.serve_forever, daemon=True)
    stub_thread.start()
    api_key_secret = f"agw_{secrets.token_urlsafe(32)}"
    with Session(engine) as session:
        owner = User(
            username="gateway-owner",
            password_hash="hash",
            is_active=True,
            approval_status=ApprovalStatus.APPROVED.value,
            must_change_password=False,
        )
        tool = Tool(slug="demo", name="演示工具", status=ToolStatus.ACTIVE.value, is_enabled=True)
        session.add_all([owner, tool])
        session.flush()
        endpoint = ApiEndpoint(tool_id=tool.id, method="POST", upstream_path="/echo", status="published")
        session.add(endpoint)
        session.flush()
        session.add_all(
            [
                ApiKey(user_id=owner.id, label="并发测试", prefix=api_key_secret[:16], secret_hash=hash_api_key(api_key_secret), scopes="demo:*", status=KeyStatus.ACTIVE.value),
                ToolUpstream(tool_id=tool.id, base_url=f"http://127.0.0.1:{stub.server_port}", auth_type="none"),
                PublishedRoute(
                    tool_id=tool.id,
                    endpoint_id=endpoint.id,
                    method="POST",
                    gateway_path="/echo",
                    upstream_path="/echo",
                    access_mode="authenticated",
                    require_idempotency_key=True,
                ),
            ]
        )
        session.commit()

    try:
        idempotency_results = _run_processes(_gateway_in_process, database_url, (api_key_secret,), (api_key_secret,))
    finally:
        stub.shutdown()
        stub.server_close()
        stub_thread.join(timeout=5)
    assert all(result[0] in {201, 409} for result in idempotency_results), idempotency_results
    assert any(result[0] == 201 for result in idempotency_results)
    assert _GatewayStub.calls == 1

    claim_results = _run_processes(_claim_in_process, database_url, (), ())
    assert all(result[0] == "ok" for result in claim_results)
    assert sum(len(result[1]) for result in claim_results) == 1

    with Session(engine) as session:
        users = session.scalars(select(User).where(User.username_key == "samename")).all()
        job = session.scalar(select(FileSyncJob).where(FileSyncJob.target_key == "poll:multiprocess"))
        idempotency_records = session.scalars(select(IdempotencyRecord)).all()
        assert len(users) == 1
        assert job is not None and job.status == "running" and job.lease_token
        assert len(idempotency_records) == 1 and idempotency_records[0].status == "completed"
    engine.dispose()


def test_multiprocess_sqlite_lock_wait_and_crashed_worker_lease_reclaim(tmp_path) -> None:
    """验证当前 SQLite 默认模式的锁等待和崩溃租约恢复，不改变生产数据库策略。"""

    database = tmp_path / "multiprocess-recovery.sqlite"
    database_url = f"sqlite:///{database.as_posix()}"
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from app.database import Base
    from app.models import FileSyncJob, PlatformSettings, Role, RoleName, User, utc_now

    engine = create_engine(database_url, connect_args={"check_same_thread": False, "timeout": 2})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Role(name=RoleName.USER.value, description="普通用户"),
                Role(name=RoleName.ADMIN.value, description="管理员"),
                PlatformSettings(id=1, smtp_enabled=True, smtp_host="smtp.test.invalid", smtp_from_email="gateway@example.test"),
                FileSyncJob(target_key="poll:crash", job_type="poll", status="pending"),
            ]
        )
        session.commit()

    context = multiprocessing.get_context("spawn")
    lock_ready = context.Event()
    lock_holder = context.Process(target=_hold_exclusive_lock, args=(str(database), lock_ready))
    lock_holder.start()
    assert lock_ready.wait(timeout=10)
    password = f"Aa1{secrets.token_urlsafe(24)}"
    started = time.monotonic()
    lock_results = _run_processes(_register_in_process, database_url, ("lock-wait-user", "lock-wait@example.test", password))
    elapsed = time.monotonic() - started
    lock_holder.join(timeout=10)
    assert lock_holder.exitcode == 0
    assert lock_results[0][0] == 201
    assert elapsed >= 0.5

    crash_ready = context.Event()
    crashed_worker = context.Process(target=_claim_and_crash, args=(database_url, crash_ready))
    crashed_worker.start()
    assert crash_ready.wait(timeout=10)
    crashed_worker.join(timeout=10)
    assert crashed_worker.exitcode == 17
    with Session(engine) as session:
        job = session.scalar(select(FileSyncJob).where(FileSyncJob.target_key == "poll:crash"))
        assert job is not None and job.status == "running" and job.lease_token
        job.lease_expires_at = utc_now() - timedelta(minutes=10)
        session.commit()

    reclaim_results = _run_processes(_claim_in_process, database_url, ())
    assert reclaim_results[0][0] == "ok"
    assert len(reclaim_results[0][1]) == 1
    with Session(engine) as session:
        assert session.scalar(select(User.id).where(User.username_key == "lock-wait-user")) is not None
        job = session.scalar(select(FileSyncJob).where(FileSyncJob.target_key == "poll:crash"))
        assert job is not None and job.status == "running" and job.lease_token
    with sqlite3.connect(database) as verification:
        assert verification.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    engine.dispose()
