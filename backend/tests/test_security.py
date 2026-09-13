import pytest

from app.schemas import ToolConfigRequest
from app.services.security import create_session_token, decode_session_token, hash_password, validate_password, verify_password


def test_password_hash_is_not_plaintext() -> None:
    password = "MvpGateway2026!"
    encoded = hash_password(password)
    assert encoded != password
    assert verify_password(password, encoded)
    assert not verify_password("WrongPassword2026!", encoded)


def test_password_policy_rejects_weak_or_username_password() -> None:
    for value in ("short", "alllowercase123", "ALLUPPERCASE123", "NoDigitsPassword"):
        try:
            validate_password(value, "liutao")
        except ValueError:
            continue
        raise AssertionError(f"弱密码不应通过: {value}")
    try:
        validate_password("LiutaoPass2026!", "liutao")
    except ValueError:
        return
    raise AssertionError("包含用户名的密码不应通过")


def test_session_token_carries_server_side_version() -> None:
    token = create_session_token("user-1", False, 3)
    payload = decode_session_token(token)
    assert payload["sub"] == "user-1"
    assert payload["mcp"] is False
    assert payload["sv"] == 3


def test_tool_token_label_rejects_header_injection_characters() -> None:
    with pytest.raises(ValueError):
        ToolConfigRequest(
            slug="demo-tool",
            name="Demo Tool",
            base_url="http://127.0.0.1:18000",
            auth_type="header_api_key",
            token_label="X-Key\r\nInjected",
        )
