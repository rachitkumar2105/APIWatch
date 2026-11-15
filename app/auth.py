"""Authentication: password hashing (bcrypt) and JWT issuance/verification.

Design notes:
- Passwords are never stored in plain text. bcrypt is deliberately slow and
  salts automatically per-password, which is why it's used instead of a fast
  general-purpose hash like SHA-256. It's called directly (passlib is
  unmaintained and breaks on bcrypt 5). bcrypt only reads the first 72 bytes
  of a password, so the schema rejects anything longer instead of silently
  ignoring the tail.
- JWTs are signed (HS256, via PyJWT) with a secret loaded from the environment.
  The decode call pins the algorithm list, so a token can't pick its own
  algorithm ("alg": "none" or an asymmetric-key confusion attack).
- In production (APP_ENV=production) the process refuses to start with the
  development default or a short secret. Locally it only logs a warning so
  tests and first runs work out of the box.
- Tokens carry the user's id ("sub"), issue time and an expiry. There is no
  refresh-token flow: access tokens are short-lived (30 min default) and a
  client must log in again after expiry. Rotation and revocation are real
  complexity that this project deliberately leaves out of scope.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from app import config

logger = logging.getLogger("apiwatch")

DEFAULT_SECRET = "dev-only-insecure-secret-change-me"
MIN_SECRET_LENGTH = 32
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))


def validate_secret(secret: str, is_production: bool) -> None:
    """Fail fast in production on a missing/weak secret; warn in development."""
    problem = None
    if secret == DEFAULT_SECRET:
        problem = "JWT_SECRET_KEY is not set (using the insecure development default)"
    elif len(secret) < MIN_SECRET_LENGTH:
        problem = f"JWT_SECRET_KEY is shorter than {MIN_SECRET_LENGTH} characters"
    if problem is None:
        return
    if is_production:
        raise RuntimeError(
            f"{problem}. Set a long random value, e.g. "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    logger.warning("%s. Fine for local development, never for a real deployment.", problem)


SECRET_KEY = os.getenv("JWT_SECRET_KEY", DEFAULT_SECRET)
validate_secret(SECRET_KEY, config.IS_PRODUCTION)

BCRYPT_MAX_BYTES = 72


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("ascii"))
    except ValueError:
        # Over-long password (bcrypt 5 refuses >72 bytes) or a malformed stored hash:
        # either way it is not a match.
        return False


def create_access_token(subject: str, expires_delta: Optional[timedelta] = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (
        expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode = {"sub": subject, "iat": now, "exp": expire}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[str]:
    """Returns the subject (user id, as a string) if valid, else None."""
    try:
        payload = jwt.decode(
            token, SECRET_KEY, algorithms=[ALGORITHM], options={"require": ["sub", "exp"]}
        )
    except jwt.PyJWTError:
        return None
    subject = payload.get("sub")
    return subject if isinstance(subject, str) else None
