import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import resend
from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from web.database import get_db
from web.models import MagicLink, Session, User

MAGIC_LINK_TTL_MINUTES = 15
SESSION_TTL_DAYS = 30
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")

resend.api_key = os.environ.get("RESEND_API_KEY", "")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def get_or_create_user(db: DBSession, email: str) -> User:
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, notify_email=email)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def create_magic_link(db: DBSession, user: User) -> str:
    token = secrets.token_urlsafe(32)
    link = MagicLink(
        user_id=user.id,
        token_hash=_hash_token(token),
        expires_at=_now() + timedelta(minutes=MAGIC_LINK_TTL_MINUTES),
    )
    db.add(link)
    db.commit()
    return token


def send_magic_link_email(email: str, token: str) -> None:
    from_email = os.environ["RESEND_FROM_EMAIL"]
    url = f"{BASE_URL}/auth/verify?token={token}"
    resend.Emails.send({
        "from": from_email,
        "to": [email],
        "subject": "Sign in to notifai",
        "text": (
            f"Click this link to sign in (expires in {MAGIC_LINK_TTL_MINUTES} minutes):\n\n"
            f"{url}\n\nIf you didn't request this, ignore this email."
        ),
    })


def verify_magic_link(db: DBSession, token: str) -> User | None:
    token_hash = _hash_token(token)
    link = db.query(MagicLink).filter(
        MagicLink.token_hash == token_hash,
        MagicLink.used_at.is_(None),
        MagicLink.expires_at > _now(),
    ).first()
    if not link:
        return None
    link.used_at = _now()
    db.commit()
    return db.query(User).filter(User.id == link.user_id).first()


def create_session(db: DBSession, user: User) -> str:
    token = secrets.token_urlsafe(32)
    session = Session(
        id=_hash_token(token),
        user_id=user.id,
        expires_at=_now() + timedelta(days=SESSION_TTL_DAYS),
    )
    db.add(session)
    db.commit()
    return token


def get_user_from_session(db: DBSession, session_id: str) -> User | None:
    if not session_id:
        return None
    session = db.query(Session).filter(
        Session.id == _hash_token(session_id),
        Session.expires_at > _now(),
    ).first()
    if not session:
        return None
    return db.query(User).filter(User.id == session.user_id).first()


def delete_session(db: DBSession, session_id: str) -> None:
    db.query(Session).filter(Session.id == _hash_token(session_id)).delete()
    db.commit()


def get_current_user(
    session_id: str | None = Cookie(default=None),
    db: DBSession = Depends(get_db),
) -> User:
    user = get_user_from_session(db, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
