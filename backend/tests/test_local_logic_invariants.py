from __future__ import annotations

import threading
import random
from collections.abc import Generator
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import FileSyncJob, User
from app.routers.auth import register
from app.schemas import RegisterRequest
from app.services.remote_files import claim_sync_jobs, sanitize_file_name


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as current:
        yield current


def test_用户名大小写变体不能同时写入数据库(session: Session) -> None:
    """用户名比较不区分大小写时，数据库也必须保持同一不变量。"""
    session.add_all(
        [
            User(username="Alice", password_hash="hash"),
            User(username="alice", password_hash="hash"),
        ]
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_注册遇到并发唯一冲突返回受控响应(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """应用层预检查失效时，数据库冲突不得穿透为未处理的 500。"""
    session.add(User(username="Alice", password_hash="hash"))
    session.commit()
    original_scalar = session.scalar

    def ignore_username_precheck(statement, *args, **kwargs):
        if "users.username_key" in str(statement):
            return None
        return original_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(session, "scalar", ignore_username_precheck)
    payload = RegisterRequest(username="alice", password="StrongPass2026!")
    request = SimpleNamespace(state=SimpleNamespace(request_id="registration-race"))

    with pytest.raises(HTTPException) as error:
        register(payload, request, session)  # type: ignore[arg-type]

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "REGISTRATION_CONFLICT"
    assert original_scalar(select(User.id).where(User.username_key == "alice")) is not None


def test_并发领取同一文件任务只返回一个持有者(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """两个 Worker 同时读取待处理任务时，只有一个可以取得有效租约。"""
    database_path = tmp_path / "file-claim-race.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as current:
        current.add(FileSyncJob(target_key="poll:race", job_type="poll", status="pending"))
        current.commit()

    barrier = threading.Barrier(2)
    original_scalars = Session.scalars

    def synchronized_scalars(self: Session, statement, *args, **kwargs):
        result = original_scalars(self, statement, *args, **kwargs)
        if "file_sync_jobs" in str(statement):
            barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(Session, "scalars", synchronized_scalars)
    claimed: list[list[str]] = []
    errors: list[Exception] = []

    def claim_once() -> None:
        try:
            with factory() as current:
                _, jobs = claim_sync_jobs(current, limit=1)
                claimed.append([item.id for item in jobs])
        except Exception as error:  # pragma: no cover - 断言失败时保留并发异常证据。
            errors.append(error)

    workers = [threading.Thread(target=claim_once) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert not errors
    assert sum(len(item) for item in claimed) == 1
    with factory() as current:
        job = current.scalar(select(FileSyncJob).where(FileSyncJob.target_key == "poll:race"))
        assert job is not None
        assert job.status == "running"
        assert job.lease_token
    engine.dispose()


def test_固定种子文件名边界输入保持安全() -> None:
    """固定随机种子覆盖控制字符、路径符号、Unicode 与超长文件名。"""
    generator = random.Random(20260826)
    alphabet = "abcXYZ012/\\. \r\n\x00中文😀"
    for _ in range(200):
        raw = "".join(generator.choice(alphabet) for _ in range(generator.randint(0, 600)))
        cleaned = sanitize_file_name(raw)
        assert cleaned
        assert len(cleaned) <= 255
        assert "/" not in cleaned and "\\" not in cleaned
        assert all(ord(char) >= 32 and ord(char) != 127 for char in cleaned)
