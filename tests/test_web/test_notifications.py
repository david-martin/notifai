"""Tests for web/notifications.py — notify_if_low_balance."""
from unittest.mock import patch
import pytest
from web.models import Query, User
from web.notifications import notify_if_low_balance


@pytest.fixture
def free_user(db):
    user = User(
        email="free@example.com",
        query_credits=10,
        tier="free",
        low_balance_notified=False,
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def paid_user(db):
    user = User(
        email="paid@example.com",
        query_credits=10,
        tier="paid",
        low_balance_notified=False,
    )
    db.add(user)
    db.commit()
    return user


def test_no_notification_when_credits_above_threshold(db, free_user):
    free_user.query_credits = 11
    db.commit()

    with patch("web.notifications.resend.Emails.send") as mock_send, \
         patch.dict("os.environ", {"RESEND_API_KEY": "test-key"}):
        result = notify_if_low_balance(free_user, db, "from@example.com")

    assert result is False
    mock_send.assert_not_called()
    assert free_user.low_balance_notified is False


def test_no_notification_when_already_notified(db, free_user):
    free_user.low_balance_notified = True
    db.commit()

    with patch("web.notifications.resend.Emails.send") as mock_send, \
         patch.dict("os.environ", {"RESEND_API_KEY": "test-key"}):
        result = notify_if_low_balance(free_user, db, "from@example.com")

    assert result is False
    mock_send.assert_not_called()


def test_sends_email_sets_flag_when_at_threshold(db, free_user):
    """Exactly 10 credits triggers the notification."""
    with patch("web.notifications.resend.Emails.send") as mock_send, \
         patch.dict("os.environ", {"RESEND_API_KEY": "test-key"}):
        result = notify_if_low_balance(free_user, db, "from@example.com")

    assert result is True
    mock_send.assert_called_once()
    db.refresh(free_user)
    assert free_user.low_balance_notified is True


def test_sends_email_when_below_threshold(db, free_user):
    free_user.query_credits = 3
    db.commit()

    with patch("web.notifications.resend.Emails.send") as mock_send, \
         patch.dict("os.environ", {"RESEND_API_KEY": "test-key"}):
        result = notify_if_low_balance(free_user, db, "from@example.com")

    assert result is True
    mock_send.assert_called_once()


def test_free_user_subject_mentions_free_credits(db, free_user):
    with patch("web.notifications.resend.Emails.send") as mock_send, \
         patch.dict("os.environ", {"RESEND_API_KEY": "test-key"}):
        notify_if_low_balance(free_user, db, "from@example.com")

    args = mock_send.call_args[0][0]
    assert "free credits" in args["subject"]


def test_paid_user_subject_does_not_mention_free(db, paid_user):
    with patch("web.notifications.resend.Emails.send") as mock_send, \
         patch.dict("os.environ", {"RESEND_API_KEY": "test-key"}):
        notify_if_low_balance(paid_user, db, "from@example.com")

    args = mock_send.call_args[0][0]
    assert "free credits" not in args["subject"]
    assert "running low" in args["subject"]


def test_active_daily_query_listed_in_body(db, free_user):
    q = Query(
        user_id=free_user.id,
        query_text="Half-Life 3 is announced by Valve",
        active=True,
        check_interval="1d",
    )
    db.add(q)
    db.commit()

    with patch("web.notifications.resend.Emails.send") as mock_send, \
         patch.dict("os.environ", {"RESEND_API_KEY": "test-key"}):
        notify_if_low_balance(free_user, db, "from@example.com")

    body = mock_send.call_args[0][0]["text"]
    assert "Half-Life 3 is announced by Valve" in body


def test_weekly_query_not_listed_in_body(db, free_user):
    q = Query(
        user_id=free_user.id,
        query_text="weekly thing",
        active=True,
        check_interval="1w",
    )
    db.add(q)
    db.commit()

    with patch("web.notifications.resend.Emails.send") as mock_send, \
         patch.dict("os.environ", {"RESEND_API_KEY": "test-key"}):
        notify_if_low_balance(free_user, db, "from@example.com")

    body = mock_send.call_args[0][0]["text"]
    assert "weekly thing" not in body


def test_no_notification_when_resend_key_missing(db, free_user):
    with patch("web.notifications.resend.Emails.send") as mock_send, \
         patch.dict("os.environ", {}, clear=True):
        result = notify_if_low_balance(free_user, db, "from@example.com")

    assert result is False
    mock_send.assert_not_called()
    # Flag must NOT be set when email wasn't sent
    db.refresh(free_user)
    assert free_user.low_balance_notified is False


def test_uses_notify_email_when_set(db, free_user):
    free_user.notify_email = "other@example.com"
    db.commit()

    with patch("web.notifications.resend.Emails.send") as mock_send, \
         patch.dict("os.environ", {"RESEND_API_KEY": "test-key"}):
        notify_if_low_balance(free_user, db, "from@example.com")

    args = mock_send.call_args[0][0]
    assert args["to"] == ["other@example.com"]


def test_flag_reset_when_send_fails(db, free_user):
    """If the Resend API call raises, low_balance_notified must be reset to False."""
    with patch("web.notifications.resend.Emails.send", side_effect=Exception("network error")), \
         patch.dict("os.environ", {"RESEND_API_KEY": "test-key"}):
        result = notify_if_low_balance(free_user, db, "from@example.com")

    assert result is False
    db.refresh(free_user)
    assert free_user.low_balance_notified is False
