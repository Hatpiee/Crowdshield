"""Phase 23, Step 4: evidence access-token generate/validate round-trip +
rejection cases (Resolution 2's generalized scoped-token mechanism),
mirroring test_stream_token.py / test_heatmap_token.py's own coverage shape
for the video/heatmap scopes."""

import uuid

import pytest

from app.core.config import settings
from app.core.security import InvalidTokenError, create_access_token, decode_access_token
from app.core.stream_token import (
    EvidenceAccessTokenError,
    HeatmapAccessTokenError,
    StreamTokenError,
    generate_evidence_access_token,
    generate_heatmap_access_token,
    generate_stream_token,
    validate_evidence_access_token,
    validate_heatmap_access_token,
    validate_stream_token,
)


def test_generate_and_validate_round_trip():
    evidence_id = uuid.uuid4()
    user_id = uuid.uuid4()
    token = generate_evidence_access_token(evidence_id, user_id)

    resolved_user_id = validate_evidence_access_token(token, evidence_id)
    assert resolved_user_id == user_id


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setattr(settings, "EVIDENCE_TOKEN_EXPIRE_MINUTES", -1)
    evidence_id = uuid.uuid4()
    token = generate_evidence_access_token(evidence_id, uuid.uuid4())

    with pytest.raises(EvidenceAccessTokenError):
        validate_evidence_access_token(token, evidence_id)


def test_token_issued_for_one_evidence_package_rejected_against_another():
    evidence_a = uuid.uuid4()
    evidence_b = uuid.uuid4()
    token = generate_evidence_access_token(evidence_a, uuid.uuid4())

    with pytest.raises(EvidenceAccessTokenError):
        validate_evidence_access_token(token, evidence_b)


def test_malformed_token_rejected():
    with pytest.raises(EvidenceAccessTokenError):
        validate_evidence_access_token("this-is-not-a-real-jwt", uuid.uuid4())


def test_tampered_token_rejected():
    evidence_id = uuid.uuid4()
    token = generate_evidence_access_token(evidence_id, uuid.uuid4())
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    with pytest.raises(EvidenceAccessTokenError):
        validate_evidence_access_token(tampered, evidence_id)


def test_a_normal_access_token_is_rejected_as_an_evidence_token():
    access_token = create_access_token(str(uuid.uuid4()), "OPERATOR")

    with pytest.raises(EvidenceAccessTokenError):
        validate_evidence_access_token(access_token, uuid.uuid4())


def test_an_evidence_token_is_rejected_as_a_normal_access_token():
    evidence_id = uuid.uuid4()
    evidence_token = generate_evidence_access_token(evidence_id, uuid.uuid4())

    with pytest.raises(InvalidTokenError):
        decode_access_token(evidence_token)


# Cross-purpose rejection (Resolution 2 / Step 4's central proof): a token
# minted for one media scope must NEVER be accepted by another scope's
# validator, even though all three now route through the same
# _generate_scoped_token/_validate_scoped_token helper internally.


def test_a_video_stream_token_is_rejected_as_an_evidence_token():
    video_id = uuid.uuid4()
    stream_token = generate_stream_token(video_id, uuid.uuid4())

    with pytest.raises(EvidenceAccessTokenError):
        validate_evidence_access_token(stream_token, video_id)


def test_an_evidence_token_is_rejected_as_a_video_stream_token():
    evidence_id = uuid.uuid4()
    evidence_token = generate_evidence_access_token(evidence_id, uuid.uuid4())

    with pytest.raises(StreamTokenError):
        validate_stream_token(evidence_token, evidence_id)


def test_a_heatmap_token_is_rejected_as_an_evidence_token():
    heatmap_id = uuid.uuid4()
    heatmap_token = generate_heatmap_access_token(heatmap_id, uuid.uuid4())

    with pytest.raises(EvidenceAccessTokenError):
        validate_evidence_access_token(heatmap_token, heatmap_id)


def test_an_evidence_token_is_rejected_as_a_heatmap_token():
    evidence_id = uuid.uuid4()
    evidence_token = generate_evidence_access_token(evidence_id, uuid.uuid4())

    with pytest.raises(HeatmapAccessTokenError):
        validate_heatmap_access_token(evidence_token, evidence_id)
