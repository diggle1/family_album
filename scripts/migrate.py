"""Create tables for v2026.10.23.1.

The first release uses SQLAlchemy ``create_all`` (no alembic revisions).
The command is idempotent: existing tables are left in place.
"""

from app.db import Base, engine
from app.models import (  # noqa: F401
    AuthCode,
    AuthCodeRequest,
    AuthThrottle,
    Child,
    Family,
    Invite,
    Membership,
    Photo,
    User,
)


def main() -> None:
    Base.metadata.create_all(engine)
    print(f"schema ready: {engine.url}")


if __name__ == "__main__":
    main()
