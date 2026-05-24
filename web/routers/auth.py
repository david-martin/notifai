import os
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session as DBSession

from web.auth import (
    create_magic_link,
    create_session,
    delete_session,
    get_current_user,
    get_or_create_user,
    send_magic_link_email,
    verify_magic_link,
)
from web.database import get_db
from web.limiter import limiter
from web.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_COOKIE = "session_id"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() != "false"


class EmailRequest(BaseModel):
    email: EmailStr


class AccountPatch(BaseModel):
    notify_email: Optional[EmailStr] = None


@router.post("/request")
@limiter.limit("5/15minutes")
def request_magic_link(request: Request, body: EmailRequest, db: DBSession = Depends(get_db)):
    user = get_or_create_user(db, body.email)
    token = create_magic_link(db, user)
    send_magic_link_email(body.email, token)
    return {"message": "Check your email for a sign-in link."}


@router.get("/verify")
@limiter.limit("20/hour")
def verify(request: Request, token: str, response: Response, db: DBSession = Depends(get_db)):
    user = verify_magic_link(db, token)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired sign-in link.")
    session_id = create_session(db, user)
    redirect = RedirectResponse(url="/dashboard.html", status_code=302)
    redirect.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=COOKIE_MAX_AGE,
    )
    return redirect


def _user_dict(user) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "notify_email": user.notify_email,
        "tier": user.tier,
        "query_credits": user.query_credits,
    }


@router.get("/me")
def me(user=Depends(get_current_user)):
    return _user_dict(user)


@router.get("/account")
def get_account(user=Depends(get_current_user)):
    return _user_dict(user)


@router.patch("/account")
def patch_account(
    body: AccountPatch,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    user.notify_email = body.notify_email
    db.commit()
    return _user_dict(user)


@router.post("/logout")
def logout(
    response: Response,
    session_id: str | None = Cookie(default=None),
    db: DBSession = Depends(get_db),
):
    if session_id:
        delete_session(db, session_id)
    response.delete_cookie(SESSION_COOKIE)
    return {"message": "Logged out."}
