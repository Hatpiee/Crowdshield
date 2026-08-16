"""Short-lived, single-purpose media-ACCESS tokens.

Phase 21, Gap 2: video-stream tokens (`generate_stream_token`/
`validate_stream_token`) — an HTML5 `<video>` tag issues a plain browser GET
request and cannot attach a custom Authorization header, so the existing
Bearer-JWT pattern used everywhere else in this app doesn't work for the
actual streaming route. Resolution: a normal authenticated call obtains
this short-lived token, then passed as a `?token=` query parameter on the
media route itself, validated independently of the main JWT flow.

Phase 22, Resolution 1: an `<img>` tag serving a heatmap snapshot hits the
EXACT SAME browser limitation — rather than build a second, parallel
mechanism, this module was REFACTORED to extract the shared
`_generate_scoped_token`/`_validate_scoped_token` helpers below, now used
by BOTH the video-specific public functions (kept with their EXACT Phase 21
signatures and external behavior — no caller of `generate_stream_token`/
`validate_stream_token` needs to change) and the new heatmap-specific ones
(`generate_heatmap_access_token`/`validate_heatmap_access_token`).

Phase 23, Resolution 2: the SAME limitation, a third time, for evidence
frame/ROI images — two more thin public wrappers
(`generate_evidence_access_token`/`validate_evidence_access_token`) call
the SAME shared helpers, purpose `"stream:evidence"`. ONE evidence token,
scoped to one `evidence_package_id`, is valid for BOTH that package's
frame image and ROI image (they are always viewed together in the
drill-down UI) — which image a request gets back is determined purely by
which route it hits (`/evidence/{id}/frame-image` vs
`/evidence/{id}/roi-image`), not encoded in the token itself.

CLAIM DESIGN: reuses PyJWT and JWT_SECRET_KEY (already a dependency/secret
since Phase 2) but each scope is a DISTINCT semantic "purpose" claim value
— `"stream:video"` and `"stream:heatmap"` (mirroring security.py's own
`"purpose": "access"`) — plus a `"resource_id"` claim (absent from access
tokens) scoping a token to the ONE video/heatmap it was minted for. A token
minted for one purpose or one resource is NEVER accepted for another:
neither scope's token replays as a normal access token (security.py's
`decode_access_token` rejects any token whose `"purpose"` isn't exactly
`"access"`), and the two media scopes don't replay as each other either.

NOTE ON THE PHASE 21 "MISSING PURPOSE CLAIM" BACKWARD-COMPATIBILITY FIX:
that fix (security.py's `decode_access_token`) was necessary because access
tokens are long-lived session credentials that could genuinely be "in
flight," held by an already-logged-in browser, across a deploy. Media
tokens here do NOT need the same treatment — they are short-lived (30
minutes by default) and minted on-demand every time a `<video>`/`<img>`
element mounts, so there is no realistic "old-style token still in active
use" scenario to protect; the internal purpose-claim string changing
(`"stream"` -> `"stream:video"`) between Phase 21 and this phase is purely
an implementation detail, invisible to every caller of the public
functions. See DECISIONS.md.
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings

ALGORITHM = "HS256"
VIDEO_STREAM_TOKEN_PURPOSE = "stream:video"
HEATMAP_ACCESS_TOKEN_PURPOSE = "stream:heatmap"
EVIDENCE_ACCESS_TOKEN_PURPOSE = "stream:evidence"


class StreamTokenError(Exception):
    """Invalid signature, expired, wrong purpose, or issued for a
    different video_id — the caller (the /stream route) treats all of
    these uniformly as a 401, per Gap 2's design."""


class HeatmapAccessTokenError(Exception):
    """Same uniform-401 treatment as StreamTokenError, for the heatmap
    /image route — invalid signature, expired, wrong purpose, or issued
    for a different heatmap_id."""


class EvidenceAccessTokenError(Exception):
    """Same uniform-401 treatment as StreamTokenError/HeatmapAccessTokenError,
    for the evidence frame-image/roi-image routes — invalid signature,
    expired, wrong purpose, or issued for a different evidence_package_id."""


def _generate_scoped_token(
    purpose: str, resource_id: uuid.UUID, user_id: uuid.UUID, expire_minutes: int
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "resource_id": str(resource_id),
        "purpose": purpose,
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=ALGORITHM)


def _validate_scoped_token(
    token: str,
    expected_purpose: str,
    resource_id: uuid.UUID,
    error_cls: type[Exception],
) -> uuid.UUID:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise error_cls("Invalid or expired token") from exc

    if payload.get("purpose") != expected_purpose:
        # Rejects a token minted for a DIFFERENT scope (a normal access
        # token, or the OTHER media-token purpose) presented here instead.
        raise error_cls(f"Token is not a valid {expected_purpose} token")

    if payload.get("resource_id") != str(resource_id):
        raise error_cls("Token was not issued for this resource")

    user_id = payload.get("sub")
    if not user_id:
        raise error_cls("Token missing subject")

    return uuid.UUID(user_id)


def generate_stream_token(video_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return _generate_scoped_token(
        VIDEO_STREAM_TOKEN_PURPOSE, video_id, user_id, settings.STREAM_TOKEN_EXPIRE_MINUTES
    )


def validate_stream_token(token: str, video_id: uuid.UUID) -> uuid.UUID:
    return _validate_scoped_token(token, VIDEO_STREAM_TOKEN_PURPOSE, video_id, StreamTokenError)


def generate_heatmap_access_token(heatmap_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return _generate_scoped_token(
        HEATMAP_ACCESS_TOKEN_PURPOSE, heatmap_id, user_id, settings.HEATMAP_TOKEN_EXPIRE_MINUTES
    )


def validate_heatmap_access_token(token: str, heatmap_id: uuid.UUID) -> uuid.UUID:
    return _validate_scoped_token(
        token, HEATMAP_ACCESS_TOKEN_PURPOSE, heatmap_id, HeatmapAccessTokenError
    )


def generate_evidence_access_token(evidence_package_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return _generate_scoped_token(
        EVIDENCE_ACCESS_TOKEN_PURPOSE,
        evidence_package_id,
        user_id,
        settings.EVIDENCE_TOKEN_EXPIRE_MINUTES,
    )


def validate_evidence_access_token(token: str, evidence_package_id: uuid.UUID) -> uuid.UUID:
    return _validate_scoped_token(
        token, EVIDENCE_ACCESS_TOKEN_PURPOSE, evidence_package_id, EvidenceAccessTokenError
    )
