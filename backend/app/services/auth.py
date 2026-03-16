"""JWT creation/verification and password hashing."""
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd.verify(plain, hashed)


def create_access_token(
    user_id: str,
    expires_minutes: int | None = None,
    purpose: str = "access",
) -> str:
    minutes = expires_minutes if expires_minutes is not None else settings.JWT_EXPIRY_MINUTES
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    claims: dict = {"sub": user_id, "exp": expire}
    if purpose != "access":
        claims["purpose"] = purpose
    return jwt.encode(claims, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str, purpose: str = "access") -> dict | None:
    """Return the full payload dict if the token is valid, else None.

    For normal access tokens the caller can read ``payload["sub"]``.
    Pass ``purpose="mfa"`` to validate short-lived MFA challenge tokens.
    """
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        token_purpose = payload.get("purpose", "access")
        if token_purpose != purpose:
            return None
        return payload
    except JWTError:
        return None
