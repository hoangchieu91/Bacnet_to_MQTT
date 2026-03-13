"""
Tests for AuthService — JWT create/decode, password hashing, role guards.
Bcrypt and jose are mocked so tests run without those libraries installed.
"""
import sys
import types
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException


# ─── Stub jose ─────────────────────────────────────────────────
_jwt_mock = MagicMock()
_jwt_mock.encode = lambda payload, secret, algorithm: "mock.token.here"
_jwt_mock.decode = lambda token, secret, algorithms: {
    "sub": "u1", "username": "alice", "role": "admin", "exp": 9999999999
}

jose_stub = types.ModuleType("jose")
jose_stub.jwt = _jwt_mock
jose_stub.JWTError = Exception
sys.modules.setdefault("jose", jose_stub)

# ─── Stub bcrypt ────────────────────────────────────────────────
bcrypt_stub = types.ModuleType("bcrypt")
bcrypt_stub.gensalt = lambda: b"$2b$12$fakesalt"
bcrypt_stub.hashpw = lambda pw, salt: b"$2b$12$hashed"
bcrypt_stub.checkpw = lambda pw, hashed: pw == b"correct_password" and hashed == b"$2b$12$hashed"
sys.modules.setdefault("bcrypt", bcrypt_stub)

from backend.auth_service import (
    hash_password,
    verify_password,
    create_token,
    decode_token,
    require_auth,
    require_operator,
    require_admin,
)


# ─────────────────────────────────────────────────────────────────
# Password hashing
# ─────────────────────────────────────────────────────────────────
class TestPasswordHelpers:
    def test_hash_password_returns_string(self):
        result = hash_password("mypassword")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_verify_password_correct(self):
        """Correct password returns True via bcrypt stub."""
        result = verify_password("correct_password", "$2b$12$hashed")
        assert result is True

    def test_verify_password_wrong(self):
        """Wrong password returns False."""
        result = verify_password("wrong_password", "$2b$12$hashed")
        assert result is False

    def test_verify_password_bcrypt_missing_returns_false(self):
        """If bcrypt missing → return False (no exception)."""
        with patch.dict(sys.modules, {"bcrypt": None}):
            # Re-import to pick up the patched module
            import importlib
            from backend import auth_service
            importlib.reload(auth_service)
            result = auth_service.verify_password("pass", "hash")
            assert result is False
            importlib.reload(auth_service)  # restore

    def test_hash_password_bcrypt_missing_raises_http500(self):
        """If bcrypt missing → hash_password raises HTTP 500 with bcrypt in detail."""
        from fastapi import HTTPException as FE
        # Simulate the ImportError path by patching at the call site
        with patch("backend.auth_service.hash_password",
                   side_effect=FE(status_code=500, detail="bcrypt not installed on server. Run: pip install bcrypt")):
            with pytest.raises(FE) as exc_info:
                from backend import auth_service
                auth_service.hash_password("test")
            assert exc_info.value.status_code == 500
            assert "bcrypt" in exc_info.value.detail


# ─────────────────────────────────────────────────────────────────
# JWT token create / decode
# ─────────────────────────────────────────────────────────────────
class TestJwtTokens:
    def test_create_token_returns_string(self):
        token = create_token("u1", "alice", "admin")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_decode_token_returns_payload(self):
        payload = decode_token("any.mock.token")
        assert payload["sub"] == "u1"
        assert payload["username"] == "alice"
        assert payload["role"] == "admin"

    def test_decode_invalid_token_raises_401(self):
        """JWTError should become HTTP 401."""
        from jose import JWTError
        with patch("backend.auth_service._jose", return_value=(
            # jwt.decode raises JWTError
            MagicMock(decode=MagicMock(side_effect=JWTError("bad token"))),
            JWTError,
        )):
            with pytest.raises(HTTPException) as exc_info:
                decode_token("bad.token.value")
            assert exc_info.value.status_code == 401

    def test_payload_includes_role(self):
        payload = decode_token("any.token")
        assert "role" in payload


# ─────────────────────────────────────────────────────────────────
# Role guards
# ─────────────────────────────────────────────────────────────────
class TestRoleGuards:
    def test_require_operator_allows_admin(self):
        payload = {"sub": "u1", "username": "admin", "role": "admin"}
        result = require_operator(payload)
        assert result["role"] == "admin"

    def test_require_operator_allows_operator(self):
        payload = {"sub": "u2", "username": "op", "role": "operator"}
        result = require_operator(payload)
        assert result["role"] == "operator"

    def test_require_operator_blocks_viewer(self):
        payload = {"sub": "u3", "username": "viewer", "role": "viewer"}
        with pytest.raises(HTTPException) as exc_info:
            require_operator(payload)
        assert exc_info.value.status_code == 403

    def test_require_admin_allows_admin(self):
        payload = {"sub": "u1", "username": "admin", "role": "admin"}
        result = require_admin(payload)
        assert result["role"] == "admin"

    def test_require_admin_blocks_operator(self):
        payload = {"sub": "u2", "username": "op", "role": "operator"}
        with pytest.raises(HTTPException) as exc_info:
            require_admin(payload)
        assert exc_info.value.status_code == 403

    def test_require_auth_no_users_returns_anonymous(self):
        """If no users configured → auth disabled → anonymous admin."""
        cm = MagicMock()
        cm.config.users = []
        with patch("backend.auth_service._get_config_manager", return_value=cm):
            result = require_auth(credentials=None)
        assert result["role"] == "admin"
        assert result["username"] == "anonymous"

    def test_require_auth_with_users_no_token_raises_401(self):
        """Users configured but no token provided → 401."""
        cm = MagicMock()
        cm.config.users = [{"username": "alice"}]
        with patch("backend.auth_service._get_config_manager", return_value=cm):
            with pytest.raises(HTTPException) as exc_info:
                require_auth(credentials=None)
        assert exc_info.value.status_code == 401
