import json
from unittest.mock import patch

import pytest

from web.models import NotificationLog, Query, User


@pytest.fixture
def authed_with_query(client, db):
    """Authenticate and create a query, returning (client, user, query)."""
    token = None
    def capture(email, t):
        nonlocal token
        token = t
    with patch("web.routers.auth.send_magic_link_email", side_effect=capture):
        client.post("/auth/request", json={"email": "user@example.com"})
    client.get(f"/auth/verify?token={token}", follow_redirects=False)

    user = db.query(User).filter(User.email == "user@example.com").first()
    query = Query(user_id=user.id, query_text="Q?", active=True)
    db.add(query)
    db.commit()
    return client, user, query


def _add_log(db, query, user, answer="NO", email_sent=False):
    log = NotificationLog(
        query_id=query.id,
        user_id=user.id,
        answer=answer,
        reason="test reason",
        sources=json.dumps(["http://example.com"]),
        email_sent=email_sent,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def test_history_requires_auth(client, db):
    user = User(email="anon@example.com")
    db.add(user)
    db.flush()
    query = Query(user_id=user.id, query_text="Q?", active=True)
    db.add(query)
    db.commit()
    response = client.get(f"/queries/{query.id}/history")
    assert response.status_code == 401


def test_history_returns_empty_list(authed_with_query):
    client, _, query = authed_with_query
    response = client.get(f"/queries/{query.id}/history")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0


def test_history_returns_log_entries(authed_with_query, db):
    client, user, query = authed_with_query
    _add_log(db, query, user, answer="YES", email_sent=True)
    _add_log(db, query, user, answer="NO", email_sent=False)

    response = client.get(f"/queries/{query.id}/history")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    # Newest first
    assert data["items"][0]["answer"] == "NO"
    assert data["items"][1]["answer"] == "YES"


def test_history_entry_fields(authed_with_query, db):
    client, user, query = authed_with_query
    _add_log(db, query, user, answer="YES", email_sent=True)

    response = client.get(f"/queries/{query.id}/history")
    item = response.json()["items"][0]
    assert "id" in item
    assert "checked_at" in item
    assert item["answer"] == "YES"
    assert item["reason"] == "test reason"
    assert item["sources"] == ["http://example.com"]
    assert item["email_sent"] is True


def test_history_pagination(authed_with_query, db):
    client, user, query = authed_with_query
    for _ in range(5):
        _add_log(db, query, user)

    response = client.get(f"/queries/{query.id}/history?page=1&page_size=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2

    response2 = client.get(f"/queries/{query.id}/history?page=2&page_size=2")
    assert len(response2.json()["items"]) == 2

    response3 = client.get(f"/queries/{query.id}/history?page=3&page_size=2")
    assert len(response3.json()["items"]) == 1


def test_history_404_wrong_user(client, db):
    other_user = User(email="other@example.com")
    db.add(other_user)
    db.flush()
    query = Query(user_id=other_user.id, query_text="Q?", active=True)
    db.add(query)
    db.commit()
    query_id = query.id

    token = None
    def capture(email, t):
        nonlocal token
        token = t
    with patch("web.routers.auth.send_magic_link_email", side_effect=capture):
        client.post("/auth/request", json={"email": "me@example.com"})
    client.get(f"/auth/verify?token={token}", follow_redirects=False)

    response = client.get(f"/queries/{query_id}/history")
    assert response.status_code == 404
