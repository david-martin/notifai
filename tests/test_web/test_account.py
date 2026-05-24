from unittest.mock import patch

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


def test_patch_account_sets_notify_email(auth_client):
    r = auth_client.patch("/auth/account", json={"notify_email": "alerts@example.com"})
    assert r.status_code == 200
    assert r.json()["notify_email"] == "alerts@example.com"


def test_patch_account_clears_notify_email(auth_client):
    auth_client.patch("/auth/account", json={"notify_email": "alerts@example.com"})
    r = auth_client.patch("/auth/account", json={"notify_email": None})
    assert r.status_code == 200
    assert r.json()["notify_email"] is None


def test_patch_account_rejects_invalid_email(auth_client):
    r = auth_client.patch("/auth/account", json={"notify_email": "not-an-email"})
    assert r.status_code == 422


def test_patch_account_requires_auth(client):
    r = client.patch("/auth/account", json={"notify_email": "x@x.com"})
    assert r.status_code == 401


def test_patch_account_persists(auth_client, db):
    from web.models import User
    auth_client.patch("/auth/account", json={"notify_email": "other@example.com"})
    me = auth_client.get("/auth/me").json()
    user = db.query(User).filter(User.id == me["id"]).first()
    assert user.notify_email == "other@example.com"
