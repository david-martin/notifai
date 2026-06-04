"""Low-balance notification helper.

Called from both the scheduled runner (_handle_result) and the manual
run endpoint (run_query_now) after each credit deduction.
"""
import logging
import os

import resend
from sqlalchemy.orm import Session as DBSession

from web.models import Query, User

logger = logging.getLogger(__name__)

LOW_BALANCE_THRESHOLD = 10


def notify_if_low_balance(user: User, db: DBSession, from_email: str) -> bool:
    """Send a low-balance email if credits <= 10 and not already notified this episode.

    Sets low_balance_notified=True and commits BEFORE sending to prevent
    double-sends if the Resend call is retried. Returns True if sent.
    """
    if user.query_credits > LOW_BALANCE_THRESHOLD or user.low_balance_notified:
        return False

    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.warning("low_balance_notify_skipped user=%s reason=no_api_key", user.email)
        return False

    if not from_email:
        logger.warning("low_balance_notify_skipped user=%s reason=no_from_email", user.email)
        return False

    active_daily = (
        db.query(Query)
        .filter(
            Query.user_id == user.id,
            Query.active == True,
            Query.check_interval == "1d",
        )
        .all()
    )

    # Mark notified before sending — prevents a second deduction in the same runner pass
    # from sending a duplicate notification for the same low-balance episode.
    user.low_balance_notified = True
    db.commit()

    notify_to = user.notify_email or user.email
    n = user.query_credits
    base_url = os.environ.get("APP_BASE_URL", "")

    if user.tier == "free":
        subject = "[notifai] your free credits are running low"
        body = _free_body(n, active_daily, base_url)
    else:
        subject = "[notifai] your credits are running low"
        body = _paid_body(n, active_daily, base_url)

    resend.api_key = api_key
    try:
        resend.Emails.send({
            "from": from_email,
            "to": [notify_to],
            "subject": subject,
            "text": body,
        })
        logger.info("low_balance_email_sent user=%s credits=%d", user.email, n)
        return True
    except Exception as exc:
        logger.error("low_balance_email_error user=%s exc=%s", user.email, exc)
        user.low_balance_notified = False
        db.commit()
        return False


def _interval_suggestion_block(active_daily: list, base_url: str) -> str:
    if not active_daily:
        return ""
    lines = [
        "\n\n---\nMake your credits go further\n\n"
        "If any of your queries aren't urgent, switching them from daily\n"
        "to weekly or monthly means they use 7× or 30× fewer credits.\n\n"
        "Your active daily queries:\n"
    ]
    for q in active_daily:
        lines.append(f'  • "{q.query_text}" — checking daily\n')
    lines.append(f"\nChange intervals from your dashboard:\n  → {base_url}/dashboard.html")
    return "".join(lines)


def _free_body(n: int, active_daily: list, base_url: str) -> str:
    s = "s" if n != 1 else ""
    return (
        f"You have {n} credit{s} left — about {n} day{s} of daily checking.\n\n"
        f"Your free credits (20) came with your account. When they're gone,\n"
        f"queries will pause until you top up.\n\n"
        f"To keep going, grab a credit pack:\n"
        f"  → {base_url}/account.html\n\n"
        f"See everything notifai has checked so far:\n"
        f"  → {base_url}/history.html"
        f"{_interval_suggestion_block(active_daily, base_url)}"
    )


def _paid_body(n: int, active_daily: list, base_url: str) -> str:
    s = "s" if n != 1 else ""
    return (
        f"You have {n} credit{s} left — about {n} day{s} at your current pace.\n\n"
        f"Top up any time:\n"
        f"  → {base_url}/account.html\n\n"
        f"See what notifai has checked:\n"
        f"  → {base_url}/history.html"
        f"{_interval_suggestion_block(active_daily, base_url)}"
    )
