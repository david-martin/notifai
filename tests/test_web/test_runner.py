import json
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from web.database import Base
from web.models import NotificationLog, Query, User
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
    user = User(email="test@example.com", notify_email="test@example.com")
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
    response = MagicMock()
    response.content = [block]
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
         patch("web.runner.get_session", return_value=runner_db):
        MockClient.return_value.messages.create.return_value = mock_resp
        from web.runner import run_checks
        run_checks()

    mock_send.assert_not_called()


def test_run_skips_inactive_queries(runner_db):
    user = User(email="test@example.com", notify_email="test@example.com")
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


def test_run_continues_after_malformed_json(runner_db):
    user = User(email="test@example.com", notify_email="test@example.com")
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
