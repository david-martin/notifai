import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from web.database import Base
from web.models import MagicLink, NotificationLog, Query, Session as SessionModel, User
import web.models  # noqa


@pytest.fixture(autouse=True)
def runner_env(monkeypatch):
    """Stub required env vars so runner tests don't exit on missing secrets."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "notifai <test@example.com>")


@pytest.fixture
def runner_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    yield db
    db.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def user_with_query(runner_db):
    user = User(email="test@example.com", notify_email="test@example.com", query_credits=10)
    runner_db.add(user)
    runner_db.flush()
    query = Query(
        user_id=user.id,
        query_text="Is this a test?",
        active=True,
    )
    runner_db.add(query)
    runner_db.commit()
    return user, query


def _mock_claude_response(answer: str, reason: str, sources: list):
    block = MagicMock()
    block.text = json.dumps({"answer": answer, "reason": reason, "sources": sources})
    block.type = "text"
    response = MagicMock()
    response.content = [block]
    response.stop_reason = "end_turn"
    response.usage.input_tokens = 100
    response.usage.output_tokens = 50
    return response


def test_run_stores_result_in_log(user_with_query, runner_db):
    _, query = user_with_query
    query_id = query.id  # capture before session close detaches the object
    mock_resp = _mock_claude_response("NO", "Not yet.", ["http://source.com"])

    with patch("web.runner.anthropic.Anthropic") as MockClient, \
         patch("web.runner.resend.Emails.send") as mock_send, \
         patch("web.runner.get_session", return_value=runner_db):
        MockClient.return_value.messages.create.return_value = mock_resp
        from web.runner import run_checks
        run_checks()

    log = runner_db.query(NotificationLog).first()
    assert log is not None
    assert log.query_id == query_id
    assert log.answer == "NO"
    assert log.email_sent is False


def test_run_sends_email_on_yes(user_with_query, runner_db):
    mock_resp = _mock_claude_response("YES", "Yes it is!", ["http://source.com"])

    with patch("web.runner.anthropic.Anthropic") as MockClient, \
         patch("web.runner.resend.Emails.send") as mock_send, \
         patch("web.runner.notify_if_low_balance"), \
         patch("web.runner.get_session", return_value=runner_db):
        MockClient.return_value.messages.create.return_value = mock_resp
        from web.runner import run_checks
        run_checks()

    mock_send.assert_called_once()
    log = runner_db.query(NotificationLog).first()
    assert log.email_sent is True


def test_run_silent_on_no(user_with_query, runner_db):
    mock_resp = _mock_claude_response("NO", "Not yet.", [])

    with patch("web.runner.anthropic.Anthropic") as MockClient, \
         patch("web.runner.resend.Emails.send") as mock_send, \
         patch("web.runner.notify_if_low_balance"), \
         patch("web.runner.get_session", return_value=runner_db):
        MockClient.return_value.messages.create.return_value = mock_resp
        from web.runner import run_checks
        run_checks()

    mock_send.assert_not_called()


def test_run_skips_inactive_queries(runner_db):
    user = User(email="test@example.com", notify_email="test@example.com", query_credits=10)
    runner_db.add(user)
    runner_db.flush()
    query = Query(user_id=user.id, query_text="Q?", active=False)
    runner_db.add(query)
    runner_db.commit()

    with patch("web.runner.anthropic.Anthropic") as MockClient, \
         patch("web.runner.resend.Emails.send"), \
         patch("web.runner.get_session", return_value=runner_db):
        from web.runner import run_checks
        run_checks()

    MockClient.return_value.messages.create.assert_not_called()


def test_run_skips_users_with_no_credits(runner_db):
    """Users with query_credits=0 are not checked."""
    user = User(email="broke@example.com", notify_email="broke@example.com", query_credits=0)
    runner_db.add(user)
    runner_db.flush()
    query = Query(user_id=user.id, query_text="Q?", active=True)
    runner_db.add(query)
    runner_db.commit()

    with patch("web.runner.anthropic.Anthropic") as MockClient, \
         patch("web.runner.resend.Emails.send"), \
         patch("web.runner.get_session", return_value=runner_db):
        from web.runner import run_checks
        run_checks()

    MockClient.return_value.messages.create.assert_not_called()


def test_run_deducts_one_credit(user_with_query, runner_db):
    """Each successful check deducts exactly 1 credit from the user."""
    user, _ = user_with_query
    user_id = user.id
    initial_credits = user.query_credits
    mock_resp = _mock_claude_response("NO", "Not yet.", [])

    with patch("web.runner.anthropic.Anthropic") as MockClient, \
         patch("web.runner.resend.Emails.send"), \
         patch("web.runner.get_session", return_value=runner_db):
        MockClient.return_value.messages.create.return_value = mock_resp
        from web.runner import run_checks
        run_checks()

    # run_checks() closes the session — re-query for fresh state
    refreshed = runner_db.query(User).filter(User.id == user_id).first()
    assert refreshed.query_credits == initial_credits - 1


def test_run_does_not_deduct_on_malformed_json(runner_db):
    """A malformed JSON response must not deduct credits."""
    user = User(email="nodeduce@example.com", notify_email="nodeduce@example.com", query_credits=5)
    runner_db.add(user)
    runner_db.flush()
    query = Query(user_id=user.id, query_text="Q?", active=True)
    runner_db.add(query)
    runner_db.commit()
    user_id = user.id

    bad_block = MagicMock()
    bad_block.text = "not json at all"
    bad_resp = MagicMock()
    bad_resp.content = [bad_block]

    with patch("web.runner.anthropic.Anthropic") as MockClient, \
         patch("web.runner.resend.Emails.send"), \
         patch("web.runner.get_session", return_value=runner_db):
        MockClient.return_value.messages.create.return_value = bad_resp
        from web.runner import run_checks
        run_checks()

    # run_checks() closes the session — re-query for fresh state
    refreshed = runner_db.query(User).filter(User.id == user_id).first()
    assert refreshed.query_credits == 5  # unchanged


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_cleanup_deletes_expired_sessions(runner_db):
    """run_checks() purges sessions past their expiry."""
    user = User(email="clean@example.com", query_credits=0)
    runner_db.add(user)
    runner_db.flush()
    # One expired, one still valid
    expired = SessionModel(
        id="expired_hash_abc123",
        user_id=user.id,
        expires_at=_now() - timedelta(days=1),
    )
    valid = SessionModel(
        id="valid_hash_abc123",
        user_id=user.id,
        expires_at=_now() + timedelta(days=10),
    )
    runner_db.add_all([expired, valid])
    runner_db.commit()

    with patch("web.runner.anthropic.Anthropic"), \
         patch("web.runner.resend.Emails.send"), \
         patch("web.runner.get_session", return_value=runner_db):
        from web.runner import run_checks
        run_checks()

    remaining = runner_db.query(SessionModel).all()
    assert len(remaining) == 1
    assert remaining[0].id == "valid_hash_abc123"


def test_cleanup_deletes_used_and_expired_magic_links(runner_db):
    """run_checks() purges used magic links and expired unused ones."""
    user = User(email="clean2@example.com", query_credits=0)
    runner_db.add(user)
    runner_db.flush()
    used = MagicLink(
        user_id=user.id,
        token_hash="used_hash",
        expires_at=_now() + timedelta(hours=1),
        used_at=_now() - timedelta(minutes=5),
    )
    expired_unused = MagicLink(
        user_id=user.id,
        token_hash="expired_hash",
        expires_at=_now() - timedelta(hours=1),
        used_at=None,
    )
    fresh = MagicLink(
        user_id=user.id,
        token_hash="fresh_hash",
        expires_at=_now() + timedelta(minutes=10),
        used_at=None,
    )
    runner_db.add_all([used, expired_unused, fresh])
    runner_db.commit()

    with patch("web.runner.anthropic.Anthropic"), \
         patch("web.runner.resend.Emails.send"), \
         patch("web.runner.get_session", return_value=runner_db):
        from web.runner import run_checks
        run_checks()

    remaining = runner_db.query(MagicLink).all()
    assert len(remaining) == 1
    assert remaining[0].token_hash == "fresh_hash"


def test_runner_skips_query_not_yet_due(runner_db):
    """A query with next_check_at in the future is not checked."""
    from datetime import datetime, timedelta, timezone
    user = User(email="future@example.com", notify_email="future@example.com", query_credits=10)
    runner_db.add(user)
    runner_db.flush()
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=5)
    query = Query(user_id=user.id, query_text="Q?", active=True, check_interval="1w", next_check_at=future)
    runner_db.add(query)
    runner_db.commit()

    with patch("web.runner.anthropic.Anthropic") as MockClient, \
         patch("web.runner.resend.Emails.send"), \
         patch("web.runner.get_session", return_value=runner_db):
        from web.runner import run_checks
        run_checks()

    MockClient.return_value.messages.create.assert_not_called()


def test_runner_runs_query_past_due(runner_db):
    """A query with next_check_at in the past is checked."""
    from datetime import datetime, timedelta, timezone
    user = User(email="past@example.com", notify_email="past@example.com", query_credits=10)
    runner_db.add(user)
    runner_db.flush()
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    query = Query(user_id=user.id, query_text="Q?", active=True, check_interval="1w", next_check_at=past)
    runner_db.add(query)
    runner_db.commit()

    mock_resp = _mock_claude_response("NO", "Not yet.", [])
    with patch("web.runner.anthropic.Anthropic") as MockClient, \
         patch("web.runner.resend.Emails.send"), \
         patch("web.runner.get_session", return_value=runner_db):
        MockClient.return_value.messages.create.return_value = mock_resp
        from web.runner import run_checks
        run_checks()

    MockClient.return_value.messages.create.assert_called_once()


def test_runner_advances_next_check_at_after_success(user_with_query, runner_db):
    """After a successful check, next_check_at is set to midnight today + interval.

    advance_interval anchors to midnight to prevent the runner firing before
    next_check_at when the batch completes a few minutes after the timer fires.
    """
    from datetime import datetime, date, timedelta, timezone
    _, query = user_with_query
    query_id = query.id
    query.check_interval = "1w"
    query.next_check_at = None
    runner_db.commit()

    mock_resp = _mock_claude_response("NO", "Not yet.", [])

    with patch("web.runner.anthropic.Anthropic") as MockClient, \
         patch("web.runner.resend.Emails.send"), \
         patch("web.runner.get_session", return_value=runner_db):
        MockClient.return_value.messages.create.return_value = mock_resp
        from web.runner import run_checks
        run_checks()

    refreshed = runner_db.query(Query).filter(Query.id == query_id).first()
    assert refreshed.next_check_at is not None
    # advance_interval anchors to midnight of today, then adds the interval.
    expected = datetime.combine(date.today(), datetime.min.time()) + timedelta(weeks=1)
    assert refreshed.next_check_at == expected


def test_runner_does_not_advance_next_check_at_on_error(runner_db):
    """A malformed JSON response must not advance next_check_at."""
    user = User(email="errtest@example.com", notify_email="errtest@example.com", query_credits=5)
    runner_db.add(user)
    runner_db.flush()
    query = Query(user_id=user.id, query_text="Q?", active=True, check_interval="1d", next_check_at=None)
    runner_db.add(query)
    runner_db.commit()
    query_id = query.id

    bad_block = MagicMock()
    bad_block.text = "not json at all"
    bad_resp = MagicMock()
    bad_resp.content = [bad_block]

    with patch("web.runner.anthropic.Anthropic") as MockClient, \
         patch("web.runner.resend.Emails.send"), \
         patch("web.runner.get_session", return_value=runner_db):
        MockClient.return_value.messages.create.return_value = bad_resp
        from web.runner import run_checks
        run_checks()

    refreshed = runner_db.query(Query).filter(Query.id == query_id).first()
    assert refreshed.next_check_at is None


def test_run_continues_after_malformed_json(runner_db):
    user = User(email="test@example.com", notify_email="test@example.com", query_credits=10)
    runner_db.add(user)
    runner_db.flush()
    for i in range(2):
        runner_db.add(Query(user_id=user.id, query_text=f"Q{i}?", active=True))
    runner_db.commit()

    bad_block = MagicMock()
    bad_block.text = "not json"
    bad_resp = MagicMock()
    bad_resp.content = [bad_block]

    good_resp = _mock_claude_response("NO", "Nope.", [])

    with patch("web.runner.anthropic.Anthropic") as MockClient, \
         patch("web.runner.resend.Emails.send"), \
         patch("web.runner.get_session", return_value=runner_db):
        MockClient.return_value.messages.create.side_effect = [bad_resp, good_resp]
        from web.runner import run_checks
        run_checks()

    # Second query should still produce a log entry
    logs = runner_db.query(NotificationLog).all()
    assert len(logs) == 1


def test_runner_triggers_low_balance_notification(user_with_query, runner_db):
    """Runner calls notify_if_low_balance after a successful credit deduction."""
    from web.models import Query, User
    user, query = user_with_query
    user.query_credits = 11   # will drop to 10 — at threshold
    user.tier = "free"
    user.low_balance_notified = False
    query.next_check_at = None
    runner_db.commit()

    mock_resp = _mock_claude_response("NO", "Not yet.", [])

    with patch("web.runner.anthropic.Anthropic") as MockClient, \
         patch("web.runner.resend.Emails.send"), \
         patch("web.runner.notify_if_low_balance") as mock_notify, \
         patch("web.runner.get_session", return_value=runner_db):
        MockClient.return_value.messages.create.return_value = mock_resp
        from web.runner import run_checks
        run_checks()

    mock_notify.assert_called_once()
