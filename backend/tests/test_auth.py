from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.deps import require_role
from app.core.security import ALGORITHM
from app.models.user import Role


def test_verify_credentials_correct_password(client, test_user):
    user, password = test_user
    response = client.post(
        "/api/v1/auth/verify-credentials",
        json={"email": user.email, "password": password},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["success"] is True
    assert isinstance(body["data"]["access_token"], str)
    assert len(body["data"]["access_token"]) > 0
    assert body["data"]["user"]["email"] == user.email


def test_verify_credentials_wrong_password(client, test_user):
    user, _ = test_user
    response = client.post(
        "/api/v1/auth/verify-credentials",
        json={"email": user.email, "password": "wrong-password"},
    )
    assert response.status_code == 401

    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_CREDENTIALS"


def test_verify_credentials_nonexistent_email(client, test_user):
    response = client.post(
        "/api/v1/auth/verify-credentials",
        json={"email": "nobody@example.com", "password": "irrelevant"},
    )
    assert response.status_code == 401

    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_CREDENTIALS"


def test_me_with_valid_token(client, test_user):
    user, password = test_user
    login = client.post(
        "/api/v1/auth/verify-credentials",
        json={"email": user.email, "password": password},
    )
    token = login.json()["data"]["access_token"]

    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    body = response.json()
    assert body["success"] is True
    assert body["data"]["email"] == user.email
    assert body["data"]["role"] == user.role.value
    assert "hashed_password" not in body["data"]
    assert "hashed_password" not in str(body)


def test_me_without_authorization_header(client, test_user):
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["success"] is False


def test_me_with_expired_or_invalid_token(client, test_user):
    user, _ = test_user
    expired_payload = {
        "sub": str(user.id),
        "role": user.role.value,
        "iat": datetime.now(timezone.utc) - timedelta(minutes=60),
        "exp": datetime.now(timezone.utc) - timedelta(minutes=30),
    }
    expired_token = jwt.encode(expired_payload, settings.JWT_SECRET_KEY, algorithm=ALGORITHM)

    response = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired_token}"}
    )
    assert response.status_code == 401

    response = client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


def test_me_with_old_style_token_missing_purpose_claim_is_still_accepted(client, test_user):
    # Phase 21 follow-up: create_access_token now adds a "purpose": "access"
    # claim, but every token issued before this phase (including any
    # session live in a running app at deploy time) has NO purpose claim at
    # all. decode_access_token must treat a MISSING claim as the default
    # access case for backward compatibility, not reject it.
    user, _ = test_user
    old_style_payload = {
        "sub": str(user.id),
        "role": user.role.value,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
    }
    old_style_token = jwt.encode(old_style_payload, settings.JWT_SECRET_KEY, algorithm=ALGORITHM)

    response = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {old_style_token}"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["email"] == user.email


def test_require_role_rejects_operator_allows_admin(make_user):
    operator = make_user("operator2@example.com", "pw", role=Role.OPERATOR)
    admin = make_user("admin2@example.com", "pw", role=Role.ADMIN)

    admin_only = require_role(Role.ADMIN)

    with pytest.raises(HTTPException) as exc_info:
        admin_only(current_user=operator)
    assert exc_info.value.status_code == 403

    assert admin_only(current_user=admin) is admin
