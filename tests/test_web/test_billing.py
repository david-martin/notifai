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
    r = client.post("/billing/checkout", json={"package": "small"})
    assert r.status_code == 401


def test_checkout_returns_url_small(auth_client):
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/test-session"

    with patch("web.routers.billing.stripe.checkout.Session.create", return_value=mock_session), \
         patch.dict("os.environ", {"STRIPE_PRICE_ID_80": "price_80"}):
        r = auth_client.post("/billing/checkout", json={"package": "small"})

    assert r.status_code == 200
    assert r.json()["url"] == "https://checkout.stripe.com/test-session"


def test_checkout_returns_url_medium(auth_client):
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/medium-session"

    with patch("web.routers.billing.stripe.checkout.Session.create", return_value=mock_session), \
         patch.dict("os.environ", {"STRIPE_PRICE_ID_160": "price_160"}):
        r = auth_client.post("/billing/checkout", json={"package": "medium"})

    assert r.status_code == 200


def test_checkout_returns_url_large(auth_client):
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/large-session"

    with patch("web.routers.billing.stripe.checkout.Session.create", return_value=mock_session), \
         patch.dict("os.environ", {"STRIPE_PRICE_ID_320": "price_320"}):
        r = auth_client.post("/billing/checkout", json={"package": "large"})

    assert r.status_code == 200


def test_checkout_unknown_package_returns_400(auth_client):
    with patch.dict("os.environ", {"STRIPE_PRICE_ID_80": "price_80"}):
        r = auth_client.post("/billing/checkout", json={"package": "platinum"})
    assert r.status_code == 400


def test_checkout_missing_price_id_returns_500(auth_client):
    """Missing STRIPE_PRICE_ID_80 env var → 500."""
    import os
    env = {k: v for k, v in os.environ.items() if k != "STRIPE_PRICE_ID_80"}
    with patch.dict("os.environ", env, clear=True):
        r = auth_client.post("/billing/checkout", json={"package": "small"})
    assert r.status_code == 500


def test_checkout_stripe_error_returns_500(auth_client):
    import stripe as stripe_mod
    with patch("web.routers.billing.stripe.checkout.Session.create",
               side_effect=stripe_mod.StripeError("card declined")), \
         patch.dict("os.environ", {"STRIPE_PRICE_ID_80": "price_80"}):
        r = auth_client.post("/billing/checkout", json={"package": "small"})
    assert r.status_code == 500


def test_checkout_creates_payment_session_not_subscription(auth_client):
    """Checkout must use mode='payment', not mode='subscription'."""
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/pay"

    with patch("web.routers.billing.stripe.checkout.Session.create", return_value=mock_session) as mock_create, \
         patch.dict("os.environ", {"STRIPE_PRICE_ID_80": "price_80"}):
        auth_client.post("/billing/checkout", json={"package": "small"})

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["mode"] == "payment"


def test_checkout_metadata_includes_credits(auth_client):
    """Checkout metadata must include user_id and credits."""
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/pay"

    with patch("web.routers.billing.stripe.checkout.Session.create", return_value=mock_session) as mock_create, \
         patch.dict("os.environ", {"STRIPE_PRICE_ID_160": "price_160"}):
        auth_client.post("/billing/checkout", json={"package": "medium"})

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["metadata"]["credits"] == "160"
    assert "user_id" in call_kwargs["metadata"]


def test_webhook_adds_credits_on_checkout_completed(client, db):
    user = User(email="buyer@example.com", query_credits=20)
    db.add(user)
    db.commit()
    user_id = user.id

    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "mode": "payment",
                "payment_status": "paid",
                "customer": "cus_abc",
                "metadata": {"user_id": user_id, "credits": "80"},
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
    assert user.query_credits == 100  # 20 + 80
    assert user.stripe_customer_id == "cus_abc"


def test_webhook_adds_160_credits(client, db):
    user = User(email="buyer160@example.com", query_credits=0)
    db.add(user)
    db.commit()
    user_id = user.id

    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "mode": "payment",
                "payment_status": "paid",
                "customer": None,
                "metadata": {"user_id": user_id, "credits": "160"},
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
    assert user.query_credits == 160


def test_webhook_ignores_subscription_events(client, db):
    """checkout.session.completed with mode=subscription must be ignored."""
    user = User(email="sub@example.com", query_credits=5)
    db.add(user)
    db.commit()
    user_id = user.id

    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "mode": "subscription",
                "payment_status": "paid",
                "customer": "cus_sub",
                "metadata": {"user_id": user_id, "credits": "80"},
            }
        },
    }

    with patch("web.routers.billing.stripe.Webhook.construct_event", return_value=event):
        client.post(
            "/billing/webhook",
            content=json.dumps(event),
            headers={"stripe-signature": "test-sig"},
        )

    db.expire(user)
    db.refresh(user)
    assert user.query_credits == 5  # unchanged


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
