from __future__ import annotations

import importlib.util
import json
import logging
import os
import smtplib
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.config import get_settings
from app.models import EmailOutbox, FileSyncJob, User
from app.services.email_delivery import _claim_due_email, _connect_smtp, deliver_due_email
from app.services.remote_files import fail_sync_job
from app.services.observability import JsonLogFormatter


def _load_migration_precheck():
    path = Path(__file__).resolve().parents[2] / "scripts" / "release_audit" / "migration_precheck.py"
    spec = importlib.util.spec_from_file_location("migration_precheck_phase7", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_external_error_text_is_redacted_before_persistence_and_audit(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(username="mail-owner", password_hash="hash", email="owner@example.test", must_change_password=False)
        session.add(user)
        session.flush()
        item = EmailOutbox(
            recipient=user.email,
            subject="验证",
            body_ciphertext="encrypted",
            next_attempt_at=user.created_at,
        )
        session.add(item)
        session.commit()

        secret = "smtp-password=very-secret-token"

        class BrokenSMTP:
            def __init__(self, *args, **kwargs):
                raise RuntimeError(f"AUTH failed {secret} Cookie=agw_session=private-cookie")

        monkeypatch.setattr("app.services.email_delivery.smtplib.SMTP", BrokenSMTP)
        monkeypatch.setattr("app.services.email_delivery.decrypt_secret", lambda _: "body")
        assert deliver_due_email(session) is True
        session.refresh(item)
        assert secret not in item.last_error
        assert "private-cookie" not in item.last_error

        audit_rows = session.execute(text("SELECT detail_json FROM admin_audit_events")).all()
        audit_text = json.dumps([row[0] for row in audit_rows], ensure_ascii=False)
        assert secret not in audit_text
        assert "private-cookie" not in audit_text


def test_smtp_connection_failure_records_protocol_stage(monkeypatch) -> None:
    """SMTP 欢迎语阶段失败时应保留可诊断阶段，且不泄露连接凭据。"""

    class BrokenSMTP:
        def __init__(self, *args, **kwargs):
            raise smtplib.SMTPServerDisconnected("banner timeout")

    monkeypatch.setattr("app.services.email_delivery.smtplib.SMTP", BrokenSMTP)
    settings = type(
        "SmtpSettings",
        (),
        {
            "smtp_host": "smtp.test.invalid",
            "smtp_port": 587,
            "smtp_use_tls": True,
            "smtp_username": "mailer@example.test",
            "smtp_password": "secret-password",
            "smtp_timeout_seconds": 3,
        },
    )()
    try:
        _connect_smtp(settings)
    except smtplib.SMTPServerDisconnected as error:
        assert getattr(error, "smtp_stage") == "connect"
    else:
        raise AssertionError("SMTP 建连失败应向调用方抛出异常")


def test_smtp_port_465_uses_implicit_tls_without_starttls(monkeypatch) -> None:
    """465 端口必须使用隐式 TLS，不能再发送 STARTTLS。"""

    calls: list[str] = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            calls.append("ssl")

        def ehlo(self):
            calls.append("ehlo")

        def starttls(self):
            raise AssertionError("465 端口不应调用 STARTTLS")

        def login(self, username, password):
            calls.append(f"login:{username}")

        def close(self):
            calls.append("close")

    monkeypatch.setattr("app.services.email_delivery.smtplib.SMTP_SSL", FakeSMTP)
    settings = type(
        "SmtpSettings",
        (),
        {
            "smtp_host": "smtp.test.invalid",
            "smtp_port": 465,
            "smtp_use_tls": True,
            "smtp_username": "mailer@example.test",
            "smtp_password": "secret-password",
            "smtp_timeout_seconds": 3,
        },
    )()
    client = _connect_smtp(settings)
    assert calls == ["ssl", "ehlo", "login:mailer@example.test"]
    client.close()


def test_email_body_decryption_failure_is_recorded_without_stuck_sending(tmp_path, monkeypatch) -> None:
    """正文密文损坏时任务应进入最终失败状态，不能遗留 sending。"""

    database = tmp_path / "email-decrypt.sqlite"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    settings = get_settings()
    monkeypatch.setattr(settings, "email_outbox_max_attempts", 1)
    monkeypatch.setattr("app.services.email_delivery.decrypt_secret", lambda _: (_ for _ in ()).throw(ValueError("密文无效")))
    with Session(engine) as session:
        item = EmailOutbox(recipient="decrypt@example.test", subject="subject", body_ciphertext="broken")
        session.add(item)
        session.commit()
        assert deliver_due_email(session) is True
        session.refresh(item)
        assert item.status == "failed"
        assert item.attempts == 1
        assert item.last_error == "SMTP_DELIVERY_FAILED:ValueError:decrypt_body"


def test_email_outbox_claim_is_single_owner_and_stale_sending_is_reclaimable(tmp_path, monkeypatch) -> None:
    """多个邮件 Worker 不得同时领取同一条记录；过期租约应可恢复。"""
    database = tmp_path / "email-claim.sqlite"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr("app.services.email_delivery.get_settings", lambda: type("Settings", (), {"email_outbox_max_attempts": 5})())
    with Session(engine) as session:
        item = EmailOutbox(recipient="claim@example.test", subject="subject", body_ciphertext="body")
        session.add(item)
        session.commit()
    with Session(engine) as first, Session(engine) as second:
        claimed = _claim_due_email(first)
        assert claimed is not None
        assert claimed.status == "sending"
        assert _claim_due_email(second) is None
        claimed.updated_at = claimed.updated_at.replace(year=claimed.updated_at.year - 1)
        first.commit()
        reclaimed = _claim_due_email(second)
        assert reclaimed is not None
        assert reclaimed.id == claimed.id


def test_worker_error_text_is_redacted_before_retry_state() -> None:
    job = FileSyncJob(target_key="poll:redact", job_type="poll", status="running", attempts=0, lease_token="lease")
    secret = "https://user:password@storage.test/private?token=secret-token"
    fail_sync_job(job, RuntimeError(f"upstream failed: {secret}"))
    assert "password" not in job.last_error.lower()
    assert "secret-token" not in job.last_error
    assert job.last_error == "FILE_SYNC_FAILED:RuntimeError"


def test_structured_log_formatter_does_not_emit_external_error_secrets() -> None:
    secret = "Authorization=Bearer very-secret-token Cookie=private-cookie"
    record = logging.LogRecord("phase7", logging.ERROR, __file__, 1, f"upstream {secret}", (), None)
    record.error = secret
    formatted = JsonLogFormatter().format(record)
    assert "very-secret-token" not in formatted
    assert "private-cookie" not in formatted


def test_username_key_rejects_unicode_normalization_equivalent_values() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                User(username="Cafe\u0301", password_hash="hash"),
                User(username="Café", password_hash="hash"),
            ]
        )
        try:
            session.flush()
        except IntegrityError:
            return
    raise AssertionError("Unicode 规范化等价用户名必须被唯一约束拒绝")


def test_migration_precheck_reports_normalized_username_conflicts_without_full_names(tmp_path) -> None:
    module = _load_migration_precheck()
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT, is_active INTEGER)")
    connection.executemany(
        "INSERT INTO users (id, username, is_active) VALUES (?, ?, ?)",
        [
            ("1", " Alice ", 1),
            ("2", "alice", 1),
            ("3", "Cafe\u0301", 1),
            ("4", "Café", 0),
        ],
    )
    connection.commit()
    connection.close()

    conflicts = module.inspect_username_conflicts(f"sqlite:///{database}")
    assert len(conflicts) == 2
    assert {item["kind"] for item in conflicts} == {"duplicate_normalized_key"}
    report = module.format_username_conflict_report(conflicts)
    assert "冲突" in report
    assert " Alice " not in report
    assert "alice" not in report
    assert "Cafe" not in report
    assert "规范键" in report


def test_migration_command_refuses_legacy_conflicts_without_partial_upgrade(tmp_path) -> None:
    database = tmp_path / "legacy-conflict.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT, is_active INTEGER)")
    connection.executemany(
        "INSERT INTO users (id, username, is_active) VALUES (?, ?, ?)",
        [("1", "Name", 1), ("2", " name ", 0)],
    )
    connection.commit()
    connection.close()

    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    result = subprocess.run(
        [sys.executable, "scripts/migrate.py"],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "username_conflict_check=failed" in output
    assert "Name" not in output
    with sqlite3.connect(database) as verification:
        assert verification.execute("SELECT COUNT(*) FROM users").fetchone() == (2,)
        assert verification.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'").fetchone() is None


def test_cleaned_legacy_database_upgrades_to_head_with_unicode_username_key_constraint(tmp_path) -> None:
    database = tmp_path / "resolved-legacy.db"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    result = subprocess.run(
        [sys.executable, "scripts/migrate.py"],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as verification:
            assert verification.execute("SELECT version_num FROM alembic_version").fetchone() == ("a6b7c8d9e0f1",)

    engine = create_engine(f"sqlite:///{database.as_posix()}")
    with Session(engine) as session:
        session.add_all([User(username="Cafe\u0301", password_hash="hash"), User(username="Café", password_hash="hash")])
        try:
            session.flush()
        except IntegrityError:
            return
    raise AssertionError("升级后的 username_key 约束未拒绝 Unicode 规范化冲突")
