"""Short-lived, single-purpose video-stream tokens — Phase 21, Gap 2.

An HTML5 `<video>` tag issues a plain browser GET request and cannot
attach a custom Authorization header, so the existing Bearer-JWT pattern
used everywhere else in this app doesn't work for the actual streaming
route. Resolution: a normal authenticated call
(`GET /videos/{id}/stream-token`) obtains this short-lived token, which is
then passed as a `?token=` query parameter on the streaming route itself
(`GET /videos/{id}/stream`), validated independently of the main JWT flow.
This is the standard, well-established solution to this exact browser
limitation (the same pattern used for signed media URLs broadly) — not a
security shortcut, and not a precedent for weakening auth elsewhere: every
OTHER route in this app still requires a normal Bearer access token.

CLAIM DESIGN: reuses PyJWT and JWT_SECRET_KEY (already a dependency/secret
since Phase 2) but is a DISTINCT semantic purpose from the main access
token — a `"purpose": "stream"` claim (mirroring security.py's own
`"purpose": "access"`, added alongside this) plus a `"video_id"` claim
(absent from access tokens) ensure a normal access token cannot be replayed
here, and — the other direction — this module's own tokens cannot be
replayed as a normal access token (security.py's `decode_access_token` now
rejects any token whose `"purpose"` isn't exactly `"access"`).
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings

ALGORITHM = "HS256"
STREAM_TOKEN_PURPOSE = "stream"


class StreamTokenError(Exception):
    """Invalid signature, expired, wrong purpose, or issued for a
    different video_id — the caller (the /stream route) treats all of
    these uniformly as a 401, per Gap 2's design."""


def generate_stream_token(video_id: uuid.UUID, user_id: uuid.UUID) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "video_id": str(video_id),
        "purpose": STREAM_TOKEN_PURPOSE,
        "iat": now,
        "exp": now + timedelta(minutes=settings.STREAM_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=ALGORITHM)


def validate_stream_token(token: str, video_id: uuid.UUID) -> uuid.UUID:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise StreamTokenError("Invalid or expired stream token") from exc

    if payload.get("purpose") != STREAM_TOKEN_PURPOSE:
        # Rejects a normal access token (or any other differently-purposed
        # token) presented here instead of a real stream token.
        raise StreamTokenError("Token is not a valid stream token")

    if payload.get("video_id") != str(video_id):
        raise StreamTokenError("Stream token was not issued for this video")

    user_id = payload.get("sub")
    if not user_id:
        raise StreamTokenError("Stream token missing subject")

    return uuid.UUID(user_id)
