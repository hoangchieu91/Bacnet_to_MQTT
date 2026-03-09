"""Authentication service — JWT + bcrypt password hashing.

Flow:
  1. POST /api/auth/login → verify user → return JWT token
  2. All protected endpoints use FastAPI Depends(require_auth / require_operator / require_admin)
  3. Token expires in 24h (configurable via JWT_EXPIRE_HOURS)

If config.users is empty → auth is DISABLED globally (backward compatible).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# ── Secret key ─────────────────────────────────────────────────
# Use env var in production; fall back to a static dev key
_SECRET = os.environ.get("GATEWAY_JWT_SECRET", "bacnet-gateway-dev-secret-change-me")
_ALGORITHM = "HS256"
_EXPIRE_HOURS = int(os.environ.get("GATEWAY_JWT_EXPIRE_HOURS", "24"))

_bearer = HTTPBearer(auto_error=False)

# Lazy imports to avoid hard dependency at module load
def _jose():
    try:
        from jose import JWTError, jwt
        return jwt, JWTError
    except ImportError:
        raise RuntimeError("python-jose not installed. Run: pip install python-jose[cryptography]")


# ── Password helpers — use bcrypt directly (passlib has bcrypt 4.x compat issues) ──
def hash_password(plain: str) -> str:
    import bcrypt
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        import bcrypt
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ── Token helpers ──────────────────────────────────────────────
def create_token(user_id: str, username: str, role: str) -> str:
    jwt, _ = _jose()
    expire = datetime.now(timezone.utc) + timedelta(hours=_EXPIRE_HOURS)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, _SECRET, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    jwt, JWTError = _jose()
    try:
        return jwt.decode(token, _SECRET, algorithms=[_ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── FastAPI dependency helpers ─────────────────────────────────
def _get_config_manager():
    """Imported lazily to avoid circular import."""
    from backend.main import config_manager
    return config_manager


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    """Returns token payload. Skips auth if no users are configured (dev mode)."""
    cm = _get_config_manager()
    users = getattr(cm.config, "users", [])

    # Auth disabled — no users configured yet
    if not users:
        return {"sub": "anonymous", "username": "anonymous", "role": "admin"}

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_token(credentials.credentials)


def require_operator(payload: dict = Depends(require_auth)) -> dict:
    if payload["role"] not in ("admin", "operator"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator or Admin role required")
    return payload


def require_admin(payload: dict = Depends(require_auth)) -> dict:
    if payload["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return payload
