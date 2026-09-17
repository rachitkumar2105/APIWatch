"""Authentication: password hashing (bcrypt) and JWT issuance/verification.

Design notes (documented here, not hidden — same honesty discipline as the
rest of the project):
- Passwords are never stored in plain text. bcrypt is deliberately slow and
  salts automatically per-password, which is why it's used instead of a fast
  general-purpose hash like SHA-256.
- JWTs are signed (HS256) with a secret loaded from the environment. In a
  real production deployment this secret would come from a secrets manager,
  not a default — the default here exists only so local dev/tests work
  out of the box, and a startup warning fires if it's still the default.
- Tokens carry the user's id ("sub") and an expiry. There is no refresh-token
  flow yet — access tokens are short-lived (30 min default) and a client
  must log in again after expiry. That's a deliberate scope decision, not an
  oversight: a refresh-token flow adds real complexity (rotation, revocation)
  that isn't needed to close out the "no auth" gap this project documented.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

logger = logging.getLogger("apiwatch")

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-only-insecure-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

if SECRET_KEY == "dev-only-insecure-secret-change-me":
    logger.warning(
        "JWT_SECRET_KEY is not set — using an insecure default. "
        "Set a real secret via the JWT_SECRET_KEY environment variable before any real deployment."
    )

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str, expires_delta: Optional[timedelta] = None) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode = {"sub": subject, "exp": expire}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[str]:
    """Returns the subject (user id, as a string) if valid, else None."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None
