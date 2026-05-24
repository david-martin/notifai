import logging
import os

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session as DBSession

from web.auth import get_current_user
from web.database import get_db
from web.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
# APP_BASE_URL is required for billing redirects. No default — must be set explicitly.
_app_base_url = os.environ.get("APP_BASE_URL", "")
SUCCESS_URL = _app_base_url + "/account.html?upgraded=1"
CANCEL_URL = _app_base_url + "/account.html"


@router.post("/checkout")
def create_checkout(
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            customer_email=user.email if not user.stripe_customer_id else None,
            customer=user.stripe_customer_id or None,
            subscription_data={"metadata": {"user_id": user.id}},
            success_url=SUCCESS_URL,
            cancel_url=CANCEL_URL,
        )
        return {"url": session.url}
    except stripe.StripeError as e:
        logger.error("Stripe checkout error for user %s: %s", user.id, e)
        raise HTTPException(status_code=500, detail="Payment provider error. Please try again.")


@router.post("/webhook")
async def stripe_webhook(request: Request, db: DBSession = Depends(get_db)):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig, STRIPE_WEBHOOK_SECRET
        )
    except stripe.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    obj = event["data"]["object"]
    event_type = event["type"]

    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        if obj.get("status") == "active":
            user_id = obj.get("metadata", {}).get("user_id")
            user = None
            if user_id:
                user = db.query(User).filter(User.id == user_id).first()
            if not user:
                user = db.query(User).filter(User.stripe_customer_id == obj.get("customer")).first()
            if user:
                user.tier = "pro"
                user.stripe_customer_id = obj.get("customer")
                user.stripe_subscription_id = obj.get("id")
                db.commit()

    elif event_type == "customer.subscription.deleted":
        customer_id = obj.get("customer")
        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if user:
            user.tier = "free"
            db.commit()

    return {"received": True}
