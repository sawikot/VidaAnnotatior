"""Passwords, sign-in sessions and one-time password links.

* Passwords are hashed with scrypt (Python's standard library), a random salt per password.
* A session is a random token in an HttpOnly cookie; the database keeps only its SHA-256, so a
  copy of the database does not let anyone sign in.
* Password links (new accounts, resets) work the same way and can be used once.
* Repeated failed sign-ins for one address are slowed down (``LoginThrottle``).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.user import PasswordToken, User, UserSession

SESSION_COOKIE = "vp_session"
MIN_PASSWORD_LENGTH = 8
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 64}


def now() -> datetime:
    """Naive UTC, as SQLite stores it."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ------------------------------------------------------------------ passwords


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"The password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > 256:
        return "The password is too long."
    return None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    try:
        _, salt_b64, digest_b64 = stored.split("$")
        salt, digest = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
    except ValueError:
        return False
    candidate = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return hmac.compare_digest(candidate, digest)


# A hash to check against when the address is unknown, so a wrong address takes as long as a wrong password.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def check_credentials(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == normalize_email(email)).first()
    ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    return user if ok and user is not None and user.is_active else None


def normalize_email(email: str) -> str:
    return email.strip().lower()


# ------------------------------------------------------------------ tokens


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, user: User, days: int) -> str:
    token = secrets.token_urlsafe(32)
    db.add(UserSession(token_hash=_digest(token), user_id=user.id, created_at=now(), expires_at=now() + timedelta(days=days)))
    user.last_login_at = now()
    db.flush()
    return token


def user_for_session(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    session = db.query(UserSession).filter(UserSession.token_hash == _digest(token)).first()
    if session is None:
        return None
    if session.expires_at < now():
        db.delete(session)
        db.commit()
        return None
    user = db.get(User, session.user_id)
    return user if user is not None and user.is_active else None


def end_session(db: Session, token: str | None) -> None:
    if token:
        db.query(UserSession).filter(UserSession.token_hash == _digest(token)).delete(synchronize_session=False)


def end_all_sessions(db: Session, user: User, keep: str | None = None) -> None:
    """Sign the person out everywhere (a password change, a disabled account) -- except ``keep``."""
    query = db.query(UserSession).filter(UserSession.user_id == user.id)
    if keep:
        query = query.filter(UserSession.token_hash != _digest(keep))
    query.delete(synchronize_session=False)


def create_password_token(db: Session, user: User, days: int = 7) -> str:
    """A new one-time link token; any older link of this person stops working."""
    db.query(PasswordToken).filter(PasswordToken.user_id == user.id).delete(synchronize_session=False)
    token = secrets.token_urlsafe(32)
    db.add(PasswordToken(token_hash=_digest(token), user_id=user.id, expires_at=now() + timedelta(days=days)))
    db.flush()
    return token


def user_for_password_token(db: Session, token: str) -> User | None:
    row = db.query(PasswordToken).filter(PasswordToken.token_hash == _digest(token)).first()
    if row is None or row.expires_at < now():
        return None
    user = db.get(User, row.user_id)
    return user if user is not None and user.is_active else None


def use_password_token(db: Session, token: str) -> None:
    db.query(PasswordToken).filter(PasswordToken.token_hash == _digest(token)).delete(synchronize_session=False)


# ------------------------------------------------------------------ throttling


class LoginThrottle:
    """At most ``limit`` failed sign-ins per key (address + client) in ``window`` seconds."""

    def __init__(self, limit: int = 8, window: float = 15 * 60) -> None:
        self.limit, self.window = limit, window
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        cutoff = time.monotonic() - self.window
        times = [t for t in self._failures.get(key, []) if t > cutoff]
        self._failures[key] = times
        return times

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._recent(key)) >= self.limit

    def failed(self, key: str) -> None:
        with self._lock:
            self._recent(key).append(time.monotonic())

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


login_throttle = LoginThrottle()
