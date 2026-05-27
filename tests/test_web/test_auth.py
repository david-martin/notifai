from datetime import datetime, timedelta
from unittest.mock import patch

import web.routers.auth as _auth_router
from web.limiter import limiter as _limiter


def test_request_magic_link_new_user(client, db):
    with patch("web.routers.auth.send_magic_link_email") as mock_send:
        response = client.post("/auth/request", json={"email": "test@example.com"})
    assert response.status_code == 200
    assert response.json() == {"message": "Check your email for a sign-in link."}
    mock_send.assert_called_once()


def test_request_magic_link_existing_user(client, db):
    # First request creates the user
    with patch("web.routers.auth.send_magic_link_email"):
        client.post("/auth/request", json={"email": "test@example.com"})
    # Second request reuses the user
    with patch("web.routers.auth.send_magic_link_email") as mock_send:
        response = client.post("/auth/request", json={"email": "test@example.com"})
    assert response.status_code == 200
    mock_send.assert_called_once()


def test_request_magic_link_same_response_unknown_email(client, db):
    # Must not reveal whether email exists (no user enumeration)
    with patch("web.routers.auth.send_magic_link_email"):
        r1 = client.post("/auth/request", json={"email": "a@example.com"})
        r2 = client.post("/auth/request", json={"email": "b@example.com"})
    assert r1.json() == r2.json()


def test_verify_valid_token(client, db):
    token = None
    def capture_token(email, t):
        nonlocal token
        token = t

    with patch("web.routers.auth.send_magic_link_email", side_effect=capture_token):
        client.post("/auth/request", json={"email": "test@example.com"})

    response = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/dashboard.html"
    assert "session_id" in response.cookies


def test_verify_invalid_token(client, db):
    response = client.get("/auth/verify?token=notarealtoken")
    assert response.status_code == 400


def test_verify_token_can_only_be_used_once(client, db):
    token = None
    def capture_token(email, t):
        nonlocal token
        token = t

    with patch("web.routers.auth.send_magic_link_email", side_effect=capture_token):
        client.post("/auth/request", json={"email": "test@example.com"})

    client.get(f"/auth/verify?token={token}", follow_redirects=False)
    response = client.get(f"/auth/verify?token={token}")
    assert response.status_code == 400


def test_me_authenticated(client, db):
    token = None
    def capture_token(email, t):
        nonlocal token
        token = t

    with patch("web.routers.auth.send_magic_link_email", side_effect=capture_token):
        client.post("/auth/request", json={"email": "test@example.com"})

    client.get(f"/auth/verify?token={token}", follow_redirects=False)
    response = client.get("/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == "test@example.com"


def test_me_unauthenticated(client, db):
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_logout(client, db):
    token = None
    def capture_token(email, t):
        nonlocal token
        token = t

    with patch("web.routers.auth.send_magic_link_email", side_effect=capture_token):
        client.post("/auth/request", json={"email": "test@example.com"})

    client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert client.get("/auth/me").status_code == 200

    client.post("/auth/logout")
    assert client.get("/auth/me").status_code == 401


def test_magic_link_rate_limit(client, db):
    _limiter.enabled = True
    _limiter.reset()
    try:
        with patch("web.routers.auth.send_magic_link_email"):
            for _ in range(5):
                r = client.post("/auth/request", json={"email": "test@example.com"})
                assert r.status_code == 200
            r = client.post("/auth/request", json={"email": "test@example.com"})
            assert r.status_code == 429
    finally:
        _limiter.enabled = False
        _limiter.reset()
