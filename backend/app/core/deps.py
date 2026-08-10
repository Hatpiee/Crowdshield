from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import error_envelope
from app.core.security import InvalidTokenError, decode_access_token
from app.models.user import Role, User


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=error_envelope("UNAUTHORIZED", "Not authenticated"),
    )


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    authorization = request.headers.get("Authorization")
    if not authorization or not authorization.startswith("Bearer "):
        raise _unauthorized()

    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except InvalidTokenError:
        raise _unauthorized()

    user_id = payload.get("sub")
    if not user_id:
        raise _unauthorized()

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized()

    return user


def require_role(*allowed_roles: Role):
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=error_envelope("FORBIDDEN", "Insufficient permissions"),
            )
        return current_user

    return dependency
