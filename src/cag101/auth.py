"""Portal authentication: Argon2id password hashing, roles, lockout, audit log.

Deliberately plain username/password with server-side sessions rather than
OAuth/SSO. This portal is intended to be handed over and hosted on-premises,
possibly air-gapped; an external identity provider would be a dependency the
receiving office cannot control. If the IS Wing later mandates SSO, the only
module that changes is this one.

Roles:
  admin      — manage users, trigger ingestion and scoring, choose the rubric
  evaluator  — view everything including restricted identity, override scores
  viewer     — read-only, no identity details, may export
"""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import select

from .config import load_config
from .db import init_db, session_scope
from .models import AuditLog, User, utcnow

ROLES = ("admin", "evaluator", "viewer")
ROLE_RANK = {"viewer": 0, "evaluator": 1, "admin": 2}

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def generate_password(length: int = 16) -> str:
    """A readable temporary password: no characters that get misread when a
    credential is written down and typed by someone else."""
    alphabet = (
        "".join(c for c in string.ascii_letters if c not in "lIO")
        + "".join(c for c in string.digits if c not in "01")
        + "!@#$%&*-+"
    )
    return "".join(secrets.choice(alphabet) for _ in range(length))


def has_role(user_role: str, required: str) -> bool:
    return ROLE_RANK.get(user_role, -1) >= ROLE_RANK.get(required, 99)


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------
def add_user(username: str, role: str = "viewer", full_name: str = "") -> str:
    """Create a user with a generated temporary password. Returns the password."""
    if role not in ROLES:
        raise ValueError(f"Role must be one of {ROLES}")
    username = username.strip().lower()
    if not username:
        raise ValueError("Username cannot be empty")

    init_db()
    password = generate_password()
    with session_scope() as session:
        if session.scalar(select(User).where(User.username == username)):
            raise ValueError(f"User {username!r} already exists")
        session.add(
            User(
                username=username,
                full_name=full_name,
                password_hash=hash_password(password),
                role=role,
                must_change_password=True,
            )
        )
        session.add(
            AuditLog(
                username="cli",
                action="user_created",
                target=username,
                detail=f"role={role}",
            )
        )
    return password


def list_users() -> list[User]:
    init_db()
    with session_scope() as session:
        return list(session.scalars(select(User).order_by(User.username)))


def set_password(username: str, password: str) -> None:
    with session_scope() as session:
        user = session.scalar(select(User).where(User.username == username.lower()))
        if user is None:
            raise ValueError(f"No such user: {username}")
        user.password_hash = hash_password(password)
        user.must_change_password = False
        user.failed_logins = 0
        user.locked_until = None
        session.add(
            AuditLog(user_id=user.id, username=user.username, action="password_changed")
        )


def bootstrap_admin() -> tuple[str, str] | None:
    """Create an administrator if none exists. Returns (username, password).

    Checks for the absence of an *admin* rather than of any user: creating a
    viewer or evaluator from the CLI first would otherwise leave the portal
    permanently without anyone able to manage users.
    """
    init_db()
    with session_scope() as session:
        existing_admin = session.scalar(
            select(User).where(User.role == "admin", User.is_active.is_(True)).limit(1)
        )
        if existing_admin is not None:
            return None
        # Reuse the name if a disabled or non-admin 'admin' row is in the way.
        username = "admin"
        clash = session.scalar(select(User).where(User.username == username))
        if clash is not None:
            suffix = 2
            while session.scalar(
                select(User).where(User.username == f"admin{suffix}")
            ) is not None:
                suffix += 1
            username = f"admin{suffix}"
    password = add_user(username, role="admin", full_name="Portal Administrator")
    return (username, password)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
class LoginError(Exception):
    """Login failed. The message is safe to show the user."""


_DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "c29tZXNhbHRzb21lc2FsdA$8Z5c3nGx1kK7pVQqW9m2Yx4vN6tR0sL8jH1aD3fE5bU"
)


def authenticate(username: str, password: str, ip: str = "") -> User:
    """Verify credentials, recording every outcome.

    The failure state is committed BEFORE LoginError is raised. It used to be
    raised inside `session_scope`, whose `except Exception: rollback()` then
    undid the very increment and audit row it had just written - so the failed
    login counter never moved, no `login_failed` row was ever recorded, and the
    lockout configured in `portal.max_failed_logins` had no effect at all.
    Verified: six wrong passwords left `failed_logins` at 0 and the account
    fully usable. An unlimited, unlogged password-guessing surface.
    """
    cfg = load_config()
    max_failed = int(cfg.get("portal.max_failed_logins", 5))
    lockout = int(cfg.get("portal.lockout_minutes", 15))

    # Same message whether the account does not exist, is disabled, or the
    # password is wrong, so the form cannot be used to enumerate accounts or
    # to discover which of them are live. The real reason goes to the audit
    # log, where an administrator can see it and an attacker cannot.
    generic = "Incorrect username or password."
    failure: str | None = None          # set to the message to raise after commit

    with session_scope() as session:
        user = session.scalar(select(User).where(User.username == username.strip().lower()))

        if user is None:
            # Verify against a dummy hash anyway. Returning early here made the
            # unknown-user path measurably faster than the wrong-password path,
            # which is a timing oracle for valid usernames.
            verify_password(_DUMMY_HASH, password)
            session.add(
                AuditLog(
                    username=username[:64], action="login_failed",
                    detail="no such user", ip=ip,
                )
            )
            failure = generic

        elif not user.is_active:
            session.add(
                AuditLog(user_id=user.id, username=user.username,
                         action="login_failed", detail="account disabled", ip=ip)
            )
            failure = generic

        else:
            now = datetime.now(timezone.utc)
            # SQLite hands back naive datetimes while Postgres returns aware
            # ones, so the stored value has to be normalised before it can be
            # compared. This crashed with a TypeError the first time the
            # lockout branch was ever reached - it had been unreachable until
            # the rollback bug above was fixed, so the two defects hid each
            # other.
            locked_until = user.locked_until
            if locked_until is not None and locked_until.tzinfo is None:
                locked_until = locked_until.replace(tzinfo=timezone.utc)

            if locked_until and locked_until > now:
                session.add(
                    AuditLog(user_id=user.id, username=user.username,
                             action="login_failed", detail="locked", ip=ip)
                )
                failure = generic

            elif not verify_password(user.password_hash, password):
                user.failed_logins += 1
                detail = f"wrong password ({user.failed_logins}/{max_failed})"
                if user.failed_logins >= max_failed:
                    user.locked_until = now + timedelta(minutes=lockout)
                    user.failed_logins = 0
                    detail = f"locked for {lockout} minutes"
                session.add(
                    AuditLog(user_id=user.id, username=user.username,
                             action="login_failed", detail=detail, ip=ip)
                )
                failure = generic

            else:
                user.failed_logins = 0
                user.locked_until = None
                user.last_login_at = utcnow()
                session.add(
                    AuditLog(user_id=user.id, username=user.username,
                             action="login", ip=ip)
                )
                session.expunge(user)
                authenticated = user

    # Outside the session: the block above has committed either way.
    if failure is not None:
        raise LoginError(failure)
    return authenticated


def get_user(user_id: int) -> User | None:
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is not None:
            session.expunge(user)
        return user


def log_action(
    user: User | None,
    action: str,
    target: str = "",
    detail: str = "",
    ip: str = "",
) -> None:
    with session_scope() as session:
        session.add(
            AuditLog(
                user_id=user.id if user else None,
                username=user.username if user else "anonymous",
                action=action,
                target=target[:128],
                detail=detail[:4000],
                ip=ip,
            )
        )
