"""One-time CLI bootstrap script to create the first ADMIN user.

Run manually from the repo root: `python scripts/create_admin.py`
Not part of the running application and not reachable via any HTTP route —
this exists solely to solve the bootstrap problem (no admin exists yet to
create the first admin through the API).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.database import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.user import Role, User  # noqa: E402


def main() -> None:
    email = input("Admin email: ").strip()
    password = input("Admin password: ").strip()
    full_name = input("Full name (optional): ").strip() or None

    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == email).first() is not None:
            print(f"A user with email {email!r} already exists.")
            return

        admin = User(
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name,
            role=Role.ADMIN,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        print(f"Created admin user {admin.email} (id={admin.id}).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
