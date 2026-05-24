import json
from unittest.mock import MagicMock, patch

import pytest

from web.models import User


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


def test_checkout_requires_auth(client):
    r = client.post("/billing/checkout")
    assert r.status_code == 401


def test_checkout_returns_url(auth_client):
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/test-session"

    with patch("web.routers.billing.stripe.checkout.Session.create", return_value=mock_session):
        r = auth_client.post("/billing/checkout")

    assert r.status_code == 200
    assert r.json()["url"] == "https://checkout.stripe.com/test-session"


def test_checkout_stripe_error_returns_500(auth_client):
    import stripe as stripe_mod
    with patch("web.routers.billing.stripe.checkout.Session.create",
               side_effect=stripe_mod.StripeError("card declined")):
        r = auth_client.post("/billing/checkout")

    assert r.status_code == 500


def test_webhook_upgrades_user_on_subscription_created(client, db):
    user = User(email="sub@example.com")
    db.add(user)
    db.commit()
    user_id = user.id

    event = {
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "id": "sub_123",
                "customer": "cus_123",
                "status": "active",
                "metadata": {"user_id": user_id},
            }
        },
    }

    with patch("web.routers.billing.stripe.Webhook.construct_event", return_value=event):
        r = client.post(
            "/billing/webhook",
            content=json.dumps(event),
            headers={"stripe-signature": "test-sig"},
        )

    assert r.status_code == 200
    db.expire(user)
    db.refresh(user)
    assert user.tier == "pro"
    assert user.stripe_customer_id == "cus_123"
    assert user.stripe_subscription_id == "sub_123"


def test_webhook_upgrades_user_on_subscription_updated(client, db):
    user = User(email="update@example.com", tier="free")
    db.add(user)
    db.commit()
    user_id = user.id

    event = {
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_456",
                "customer": "cus_456",
                "status": "active",
                "metadata": {"user_id": user_id},
            }
        },
    }

    with patch("web.routers.billing.stripe.Webhook.construct_event", return_value=event):
        r = client.post(
            "/billing/webhook",
            content=json.dumps(event),
            headers={"stripe-signature": "test-sig"},
        )

    assert r.status_code == 200
    db.expire(user)
    db.refresh(user)
    assert user.tier == "pro"


def test_webhook_downgrades_user_on_subscription_deleted(client, db):
    user = User(
        email="cancel@example.com",
        tier="pro",
        stripe_customer_id="cus_789",
        stripe_subscription_id="sub_789",
    )
    db.add(user)
    db.commit()

    event = {
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "id": "sub_789",
                "customer": "cus_789",
                "status": "canceled",
                "metadata": {},
            }
        },
    }

    with patch("web.routers.billing.stripe.Webhook.construct_event", return_value=event):
        r = client.post(
            "/billing/webhook",
            content=json.dumps(event),
            headers={"stripe-signature": "test-sig"},
        )

    assert r.status_code == 200
    db.expire(user)
    db.refresh(user)
    assert user.tier == "free"


def test_webhook_invalid_signature_returns_400(client):
    import stripe as stripe_mod
    with patch("web.routers.billing.stripe.Webhook.construct_event",
               side_effect=stripe_mod.SignatureVerificationError("bad sig", "sig")):
        r = client.post(
            "/billing/webhook",
            content="{}",
            headers={"stripe-signature": "bad"},
        )
    assert r.status_code == 400
