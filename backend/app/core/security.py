from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings

ALGORITHM = "HS256"


class InvalidTokenError(Exception):
    pass


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        # Phase 21: an explicit "purpose" claim, added retroactively to
        # this Phase 2 function. Reason: Phase 21 introduces a SECOND,
        # differently-scoped token type (video stream tokens,
        # stream_token.py) signed with this SAME JWT_SECRET_KEY. Without a
        # distinct purpose marker, a stream token — which also carries a
        # "sub" claim — would be silently accepted by decode_access_token
        # below (and therefore get_current_user) as if it were a normal
        # access token, and vice versa. See DECISIONS.md.
        "purpose": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("Invalid or expired token") from exc

    if payload.get("purpose", "access") != "access":
        # A MISSING purpose claim defaults to "access" — backward
        # compatible with every token issued before this phase, which
        # never carried this claim at all. Only an EXPLICIT, different
        # purpose (e.g. "stream") is rejected here.
        raise InvalidTokenError("Token is not a valid access token")
    return payload
