import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import error_envelope, success_envelope
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.schemas.user import LoginRequest, TokenResponse, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=error_envelope("INVALID_CREDENTIALS", "Invalid email or password"),
    )


@router.post("/verify-credentials")
def verify_credentials(body: LoginRequest, db: Session = Depends(get_db)):
    """Internal endpoint — called server-side only by Auth.js's Credentials
    provider (frontend/auth.ts). Never call this directly from browser JS:
    it exists purely so FastAPI can independently verify credentials and
    mint its own JWT, without trusting a frontend-asserted identity."""
    user = db.query(User).filter(User.email == body.email).first()
    # Respond identically whether the user doesn't exist or the password is
    # wrong, to avoid leaking which case occurred.
    if user is None or not verify_password(body.password, user.hashed_password):
        raise _invalid_credentials()

    access_token = create_access_token(user_id=str(user.id), role=user.role.value)
    token_response = TokenResponse(
        access_token=access_token, user=UserRead.model_validate(user)
    )
    return success_envelope(token_response.model_dump(mode="json"))


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return success_envelope(UserRead.model_validate(current_user).model_dump(mode="json"))


@router.post("/logout")
def logout(current_user: User = Depends(get_current_user)):
    # V1 JWTs are stateless: this endpoint cannot and does not invalidate the
    # token server-side (no blocklist in scope for V1). Real session
    # termination happens client-side when Auth.js discards its session.
    logger.info("User %s logged out", current_user.id)
    return success_envelope({"message": "Logged out"})
