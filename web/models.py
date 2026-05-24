from datetime import datetime, date, timezone
from uuid import uuid4
from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, String

from web.database import Base


def _uuid():
    return str(uuid4())


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=_uuid)
    email = Column(String, unique=True, nullable=False, index=True)
    notify_email = Column(String, nullable=True)
    tier = Column(String, nullable=False, default="free")  # legacy, kept for migration
    # Query credits: each scheduled or on-demand check costs 1 credit.
    # Purchased in one-time Stripe blocks (80 / 160 / 320). New accounts start at 0.
    query_credits = Column(Integer, nullable=False, default=0)
    creation_attempts_this_month = Column(Integer, nullable=False, default=0)
    creation_attempts_reset_at = Column(Date, nullable=True)
    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)


class MagicLink(Base):
    __tablename__ = "magic_links"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    token_hash = Column(String, unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)


class Query(Base):
    __tablename__ = "queries"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    query_text = Column(String, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    notify_on_no = Column(Boolean, nullable=False, default=False)
    # completed=True means the runner answered YES and auto-deactivated the query.
    # Distinct from active=False (manually paused). User can re-enable either way.
    completed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=_now)


class NotificationLog(Base):
    __tablename__ = "notification_log"

    id = Column(String(36), primary_key=True, default=_uuid)
    query_id = Column(String(36), ForeignKey("queries.id"), nullable=False)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    checked_at = Column(DateTime, nullable=False, default=_now)
    answer = Column(String, nullable=False)
    reason = Column(String, nullable=True)
    sources = Column(String, nullable=True)
    email_sent = Column(Boolean, nullable=False, default=False)
