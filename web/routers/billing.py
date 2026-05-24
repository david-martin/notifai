import logging
import os

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from web.auth import get_current_user
from web.database import get_db
from web.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
_app_base_url = os.environ.get("APP_BASE_URL", "")
SUCCESS_URL = _app_base_url + "/account.html?purchased=1"
CANCEL_URL = _app_base_url + "/account.html"

# One-time purchase packages: credits → Stripe price ID env var.
# STRIPE_PRICE_ID_80:  €4  → 80 credits
# STRIPE_PRICE_ID_160: €8  → 160 credits
# STRIPE_PRICE_ID_320: €16 → 320 credits
PACKAGES = {
    "small":  {"credits": 80,  "price_id_env": "STRIPE_PRICE_ID_80"},
    "medium": {"credits": 160, "price_id_env": "STRIPE_PRICE_ID_160"},
    "large":  {"credits": 320, "price_id_env": "STRIPE_PRICE_ID_320"},
}


class CheckoutRequest(BaseModel):
    package: str  # "small" | "medium" | "large"


@router.post("/checkout")
def create_checkout(
    body: CheckoutRequest,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    pkg = PACKAGES.get(body.package)
    if not pkg:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown package '{body.package}'. Must be one of: small, medium, large.",
        )

    price_id = os.environ.get(pkg["price_id_env"], "")
    if not price_id:
        logger.error("Missing env var %s for package %s", pkg["price_id_env"], body.package)
        raise HTTPException(
            status_code=500,
            detail="Payment provider not configured. Please try again later.",
        )

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            customer_email=user.email if not user.stripe_customer_id else None,
            customer=user.stripe_customer_id or None,
            metadata={"user_id": user.id, "credits": str(pkg["credits"])},
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
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except stripe.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "checkout.session.completed":
        obj = event["data"]["object"]
        # Only process one-time payment sessions that have been paid.
        if obj.get("mode") == "payment" and obj.get("payment_status") == "paid":
            user_id = obj.get("metadata", {}).get("user_id")
            credits_str = obj.get("metadata", {}).get("credits", "0")
            try:
                credits = int(credits_str)
            except (ValueError, TypeError):
                logger.error("Invalid credits value in checkout metadata: %r", credits_str)
                return {"received": True}

            user = None
            if user_id:
                user = db.query(User).filter(User.id == user_id).first()
            if not user:
                customer_id = obj.get("customer")
                if customer_id:
                    user = db.query(User).filter(User.stripe_customer_id == customer_id).first()

            if user:
                if obj.get("customer") and not user.stripe_customer_id:
                    user.stripe_customer_id = obj.get("customer")
                user.query_credits += credits
                db.commit()
                logger.info(
                    "Added %d credits to user %s (total: %d)",
                    credits, user.id, user.query_credits,
                )
            else:
                logger.error(
                    "No user found for checkout.session.completed: user_id=%s", user_id
                )

    return {"received": True}
