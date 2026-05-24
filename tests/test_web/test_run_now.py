"""Tests for POST /queries/{query_id}/run — the "Run now" endpoint."""
import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def auth_client(client, db):
    token = None

    def capture(email, t):
        nonlocal token
        token = t

    with patch("web.routers.auth.send_magic_link_email", side_effect=capture):
        client.post("/auth/request", json={"email": "user@example.com"})
    client.get(f"/auth/verify?token={token}", follow_redirects=False)
    return client


@pytest.fixture
def query_id(auth_client, db):
    """Create a query owned by the authenticated user and return its ID."""
    from web.models import Query, User
    user = db.query(User).filter(User.email == "user@example.com").first()
    q = Query(user_id=user.id, query_text="Has Python 4.0 been officially released?", active=True)
    db.add(q)
    db.commit()
    return q.id


def _mock_claude(answer: str, reason: str = "Test reason.", sources: list = None):
    block = MagicMock()
    block.text = json.dumps({
        "answer": answer,
        "reason": reason,
        "sources": sources or ["http://example.com"],
    })
    resp = MagicMock()
    resp.content = [block]
    return resp


def test_run_now_returns_result(auth_client, query_id):
    with patch("web.routers.queries.anthropic_client") as mock_client:
        mock_client.messages.create.return_value = _mock_claude("NO", "Not yet.")
        r = auth_client.post(f"/queries/{query_id}/run")
    assert r.status_code == 200
    data = r.json()
    assert data["answer"] == "NO"
    assert data["reason"] == "Not yet."
    assert "checked_at" in data


def test_run_now_logs_to_notification_log(auth_client, query_id, db):
    from web.models import NotificationLog
    with patch("web.routers.queries.anthropic_client") as mock_client:
        mock_client.messages.create.return_value = _mock_claude("NO", "Not yet.")
        auth_client.post(f"/queries/{query_id}/run")

    log = db.query(NotificationLog).filter(NotificationLog.query_id == query_id).first()
    assert log is not None
    assert log.answer == "NO"
    assert log.email_sent is False


def test_run_now_sends_email_on_yes(auth_client, query_id, db):
    with patch("web.routers.queries.anthropic_client") as mock_client, \
         patch("web.routers.queries.resend.Emails.send") as mock_send:
        mock_client.messages.create.return_value = _mock_claude("YES", "It's out!")
        # Provide required email env vars
        with patch.dict("os.environ", {
            "RESEND_FROM_EMAIL": "notifai <test@example.com>",
            "RESEND_API_KEY": "re_test",
        }):
            r = auth_client.post(f"/queries/{query_id}/run")

    assert r.status_code == 200
    mock_send.assert_called_once()
    data = r.json()
    assert data["answer"] == "YES"
    assert data["email_sent"] is True


def test_run_now_does_not_deactivate_on_yes(auth_client, query_id, db):
    from web.models import Query
    with patch("web.routers.queries.anthropic_client") as mock_client, \
         patch("web.routers.queries.resend.Emails.send"), \
         patch.dict("os.environ", {
             "RESEND_FROM_EMAIL": "notifai <test@example.com>",
             "RESEND_API_KEY": "re_test",
         }):
        mock_client.messages.create.return_value = _mock_claude("YES", "Released!")
        auth_client.post(f"/queries/{query_id}/run")

    db.expire_all()
    q = db.query(Query).filter(Query.id == query_id).first()
    assert q.active is True  # query stays active — no auto-deactivation on manual run
    assert q.completed is False


def test_run_now_requires_auth(client):
    # Use a fresh unauthenticated client — any UUID is fine; auth check fires first
    r = client.post("/queries/00000000-0000-0000-0000-000000000000/run")
    assert r.status_code == 401


def test_run_now_404_for_other_users_query(client, db, query_id):
    """A different user cannot run another user's query."""
    # Sign in as a second user
    token = None

    def capture(email, t):
        nonlocal token
        token = t

    with patch("web.routers.auth.send_magic_link_email", side_effect=capture):
        client.post("/auth/request", json={"email": "other@example.com"})
    client.get(f"/auth/verify?token={token}", follow_redirects=False)

    r = client.post(f"/queries/{query_id}/run")
    assert r.status_code == 404


def test_run_now_uses_real_time_api_ignores_batch_flag(auth_client, query_id):
    """Endpoint always calls messages.create, never the batch API."""
    with patch("web.routers.queries.anthropic_client") as mock_client, \
         patch.dict("os.environ", {"NOTIFAI_USE_BATCH": "true"}):
        mock_client.messages.create.return_value = _mock_claude("NO")
        auth_client.post(f"/queries/{query_id}/run")

    mock_client.messages.create.assert_called_once()
    mock_client.messages.batches.create.assert_not_called()
