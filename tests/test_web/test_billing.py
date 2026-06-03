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
    r = client.post("/billing/checkout", json={"package": "starter"})
    assert r.status_code == 401


def test_checkout_returns_url_starter(auth_client):
    """New 'starter' package (90 credits / €2) returns a checkout URL."""
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/test-session"
    with patch("web.routers.billing.stripe.checkout.Session.create", return_value=mock_session), \
         patch.dict("os.environ", {"STRIPE_PRICE_ID_90": "price_test_90"}):
        r = auth_client.post("/billing/checkout", json={"package": "starter"})
    assert r.status_code == 200
    assert r.json()["url"] == "https://checkout.stripe.com/test-session"


def test_checkout_returns_url_standard(auth_client):
    """New 'standard' package (270 credits / €5) returns a checkout URL."""
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/test-session"
    with patch("web.routers.billing.stripe.checkout.Session.create", return_value=mock_session), \
         patch.dict("os.environ", {"STRIPE_PRICE_ID_270": "price_test_270"}):
        r = auth_client.post("/billing/checkout", json={"package": "standard"})
    assert r.status_code == 200
    assert r.json()["url"] == "https://checkout.stripe.com/test-session"


def test_checkout_returns_url_plus(auth_client):
    """New 'plus' package (600 credits / €10) returns a checkout URL."""
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/test-session"
    with patch("web.routers.billing.stripe.checkout.Session.create", return_value=mock_session), \
         patch.dict("os.environ", {"STRIPE_PRICE_ID_600": "price_test_600"}):
        r = auth_client.post("/billing/checkout", json={"package": "plus"})
    assert r.status_code == 200
    assert r.json()["url"] == "https://checkout.stripe.com/test-session"


def test_checkout_unknown_package_returns_400(auth_client):
    with patch.dict("os.environ", {"STRIPE_PRICE_ID_90": "price_test_90"}):
        r = auth_client.post("/billing/checkout", json={"package": "platinum"})
    assert r.status_code == 400


def test_checkout_missing_price_id_returns_500(auth_client):
    """Missing STRIPE_PRICE_ID_90 env var → 500."""
    import os
    env = {k: v for k, v in os.environ.items() if k != "STRIPE_PRICE_ID_90"}
    with patch.dict("os.environ", env, clear=True):
        r = auth_client.post("/billing/checkout", json={"package": "starter"})
    assert r.status_code == 500


def test_checkout_stripe_error_returns_500(auth_client):
    import stripe as stripe_mod
    with patch("web.routers.billing.stripe.checkout.Session.create",
               side_effect=stripe_mod.StripeError("card declined")), \
         patch.dict("os.environ", {"STRIPE_PRICE_ID_90": "price_test_90"}):
        r = auth_client.post("/billing/checkout", json={"package": "starter"})
    assert r.status_code == 500


def test_checkout_creates_payment_session_not_subscription(auth_client):
    """Checkout must use mode='payment', not mode='subscription'."""
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/pay"

    with patch("web.routers.billing.stripe.checkout.Session.create", return_value=mock_session) as mock_create, \
         patch.dict("os.environ", {"STRIPE_PRICE_ID_90": "price_test_90"}):
        auth_client.post("/billing/checkout", json={"package": "starter"})

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["mode"] == "payment"


def test_checkout_metadata_includes_credits(auth_client):
    """Checkout metadata must include user_id and credits."""
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/pay"

    with patch("web.routers.billing.stripe.checkout.Session.create", return_value=mock_session) as mock_create, \
         patch.dict("os.environ", {"STRIPE_PRICE_ID_270": "price_test_270"}):
        auth_client.post("/billing/checkout", json={"package": "standard"})

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["metadata"]["credits"] == "270"
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
                "metadata": {"user_id": user_id, "credits": "90"},
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
    assert user.query_credits == 110  # 20 + 90
    assert user.stripe_customer_id == "cus_abc"


def test_webhook_adds_270_credits(client, db):
    user = User(email="buyer270@example.com", query_credits=0)
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
                "metadata": {"user_id": user_id, "credits": "270"},
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
    assert user.query_credits == 270


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
                "metadata": {"user_id": user_id, "credits": "90"},
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


def test_webhook_rejects_unknown_credit_value(client, db):
    """Credits value not in {90, 270, 600} is rejected — no credits added."""
    user = User(email="creditbuyer@example.com", query_credits=0)
    db.add(user)
    db.commit()

    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "mode": "payment",
                "payment_status": "paid",
                "customer": None,
                "metadata": {"user_id": user.id, "credits": "9999"},
            }
        },
    }

    with patch("web.routers.billing.stripe.Webhook.construct_event", return_value=event):
        r = client.post(
            "/billing/webhook",
            content=b"payload",
            headers={"stripe-signature": "sig"},
        )

    assert r.status_code == 200
    db.expire(user)
    db.refresh(user)
    assert user.query_credits == 0  # no credits added for unexpected value


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


def test_webhook_sets_tier_paid_on_purchase(client, db):
    """Successful purchase must set user.tier = 'paid'."""
    user = User(email="buyer@example.com", query_credits=0, tier="free")
    db.add(user)
    db.commit()
    user_id = user.id

    event = {
        "type": "checkout.session.completed",
        "data": {"object": {
            "mode": "payment", "payment_status": "paid",
            "customer": None,
            "metadata": {"user_id": user_id, "credits": "90"},
        }},
    }
    with patch("web.routers.billing.stripe.Webhook.construct_event", return_value=event):
        client.post("/billing/webhook", content=json.dumps(event),
                    headers={"stripe-signature": "sig"})

    db.expire(user)
    db.refresh(user)
    assert user.tier == "paid"


def test_webhook_resets_low_balance_notified_on_purchase(client, db):
    """Purchase resets low_balance_notified so user gets notified again next episode."""
    user = User(email="buyer2@example.com", query_credits=5, tier="free",
                low_balance_notified=True)
    db.add(user)
    db.commit()
    user_id = user.id

    event = {
        "type": "checkout.session.completed",
        "data": {"object": {
            "mode": "payment", "payment_status": "paid",
            "customer": None,
            "metadata": {"user_id": user_id, "credits": "90"},
        }},
    }
    with patch("web.routers.billing.stripe.Webhook.construct_event", return_value=event):
        client.post("/billing/webhook", content=json.dumps(event),
                    headers={"stripe-signature": "sig"})

    db.expire(user)
    db.refresh(user)
    assert user.low_balance_notified is False
