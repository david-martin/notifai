from unittest.mock import patch
import pytest


@pytest.fixture
def auth_client(client, db):
    """Client with an authenticated session."""
    token = None
    def capture(email, t):
        nonlocal token
        token = t

    with patch("web.routers.auth.send_magic_link_email", side_effect=capture):
        client.post("/auth/request", json={"email": "user@example.com"})
    client.get(f"/auth/verify?token={token}", follow_redirects=False)
    return client


def test_list_queries_empty(auth_client):
    response = auth_client.get("/queries")
    assert response.status_code == 200
    assert response.json() == []


def test_create_query(auth_client):
    response = auth_client.post("/queries", json={"query_text": "Is this a test?"})
    assert response.status_code == 201
    data = response.json()
    assert data["query_text"] == "Is this a test?"
    assert data["active"] is True


def test_list_queries_returns_created(auth_client):
    auth_client.post("/queries", json={"query_text": "Is this Q1 a test?"})
    auth_client.post("/queries", json={"query_text": "Is this Q2 a test?"})
    response = auth_client.get("/queries")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_toggle_query_active(auth_client):
    create = auth_client.post("/queries", json={"query_text": "Is this Q1 a test?"})
    query_id = create.json()["id"]

    response = auth_client.patch(f"/queries/{query_id}", json={"active": False})
    assert response.status_code == 200
    assert response.json()["active"] is False


def test_delete_query(auth_client):
    create = auth_client.post("/queries", json={"query_text": "Is this Q1 a test?"})
    query_id = create.json()["id"]

    response = auth_client.delete(f"/queries/{query_id}")
    assert response.status_code == 204

    assert auth_client.get("/queries").json() == []


def test_free_tier_limit_two_active_queries(auth_client):
    auth_client.post("/queries", json={"query_text": "Is this Q1 a test?"})
    auth_client.post("/queries", json={"query_text": "Is this Q2 a test?"})
    response = auth_client.post("/queries", json={"query_text": "Is this Q3 a test?"})
    assert response.status_code == 429


def test_free_tier_limit_allows_after_toggle_inactive(auth_client):
    r1 = auth_client.post("/queries", json={"query_text": "Is this Q1 a test?"})
    r2 = auth_client.post("/queries", json={"query_text": "Is this Q2 a test?"})
    auth_client.patch(f"/queries/{r1.json()['id']}", json={"active": False})
    response = auth_client.post("/queries", json={"query_text": "Is this Q3 a test?"})
    assert response.status_code == 201


def test_cannot_access_other_users_query(client, db):
    tokens = {}
    def capture(email, t):
        tokens[email] = t

    with patch("web.routers.auth.send_magic_link_email", side_effect=capture):
        client.post("/auth/request", json={"email": "user1@example.com"})
        client.post("/auth/request", json={"email": "user2@example.com"})

    # Authenticate as user1, create a query
    client.get(f"/auth/verify?token={tokens['user1@example.com']}", follow_redirects=False)
    create = client.post("/queries", json={"query_text": "Is this Q1 a test?"})
    query_id = create.json()["id"]
    client.post("/auth/logout")

    # Authenticate as user2, try to access user1's query
    client.get(f"/auth/verify?token={tokens['user2@example.com']}", follow_redirects=False)
    response = client.delete(f"/queries/{query_id}")
    assert response.status_code == 404


def test_requires_auth(client):
    response = client.get("/queries")
    assert response.status_code == 401
