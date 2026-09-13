from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings


@pytest.fixture(autouse=True)
def test_runtime_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧测试统一运行在隔离邮件环境，生产默认仍保持公开注册关闭。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "smtp.test.invalid")
    monkeypatch.setattr(settings, "smtp_from", "gateway@example.test")
    monkeypatch.setattr(settings, "email_test_mode", True)
