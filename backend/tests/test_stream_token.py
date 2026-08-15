"""Phase 21, Step 6: stream token generate/validate round-trip + rejection
cases (Gap 2's claim-design requirements)."""

import uuid

import pytest

from app.core.config import settings
from app.core.security import InvalidTokenError, create_access_token, decode_access_token
from app.core.stream_token import StreamTokenError, generate_stream_token, validate_stream_token


def test_generate_and_validate_round_trip():
    video_id = uuid.uuid4()
    user_id = uuid.uuid4()
    token = generate_stream_token(video_id, user_id)

    resolved_user_id = validate_stream_token(token, video_id)
    assert resolved_user_id == user_id


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setattr(settings, "STREAM_TOKEN_EXPIRE_MINUTES", -1)
    video_id = uuid.uuid4()
    token = generate_stream_token(video_id, uuid.uuid4())

    with pytest.raises(StreamTokenError):
        validate_stream_token(token, video_id)


def test_token_issued_for_one_video_rejected_against_another():
    video_id = uuid.uuid4()
    other_video_id = uuid.uuid4()
    token = generate_stream_token(video_id, uuid.uuid4())

    with pytest.raises(StreamTokenError):
        validate_stream_token(token, other_video_id)


def test_malformed_token_rejected():
    with pytest.raises(StreamTokenError):
        validate_stream_token("this-is-not-a-real-jwt", uuid.uuid4())


def test_tampered_token_rejected():
    video_id = uuid.uuid4()
    token = generate_stream_token(video_id, uuid.uuid4())
    # Flip the last character of the signature — still well-formed (3
    # base64 segments), but the signature no longer verifies.
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    with pytest.raises(StreamTokenError):
        validate_stream_token(tampered, video_id)


def test_a_normal_access_token_is_rejected_as_a_stream_token():
    # Proves the claim-design requirement: a main access token (same
    # secret, same algorithm, genuinely valid) must NOT be replayable as a
    # stream token, since it lacks the "purpose": "stream" claim.
    access_token = create_access_token(str(uuid.uuid4()), "OPERATOR")

    with pytest.raises(StreamTokenError):
        validate_stream_token(access_token, uuid.uuid4())


def test_a_stream_token_is_rejected_as_a_normal_access_token():
    # The other direction of the same claim-design requirement: a stream
    # token (same secret/algorithm, genuinely valid, carries "sub" too)
    # must NOT be replayable as a normal access token.
    video_id = uuid.uuid4()
    stream_token = generate_stream_token(video_id, uuid.uuid4())

    with pytest.raises(InvalidTokenError):
        decode_access_token(stream_token)
