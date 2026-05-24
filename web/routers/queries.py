import json
import os
import re
from datetime import date

import anthropic
from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from web.auth import get_current_user
from web.database import get_db
from web.limiter import limiter
from web.models import NotificationLog, Query, User

router = APIRouter(prefix="/queries", tags=["queries"])

# Configuration — read from env with sensible defaults.
FREE_TIER_QUERY_LIMIT = int(os.environ.get("FREE_TIER_QUERY_LIMIT", "2"))
FREE_TIER_CREATION_LIMIT = int(os.environ.get("FREE_TIER_CREATION_LIMIT", "10"))
ASSIST_MODEL = os.environ.get("NOTIFAI_ASSIST_MODEL", "claude-haiku-4-5-20251001")

anthropic_client = anthropic.Anthropic()

GUARD_SYSTEM_PROMPT = (
    "You evaluate user-submitted descriptions for an event notification service. "
    "A valid description is one where a web search could plausibly return a yes/no answer — "
    "something real-world, specific enough to check, and that could become true in the future. "
    "Be permissive: if the intent is clear and a web search could answer it, it is valid. "
    "Do not reject descriptions just because they could be phrased more precisely. "
    "Rules: "
    "(1) If the intent is clear and the event is checkable via web search, return valid=true. "
    "(2) If it is phrased as a question, or is genuinely ambiguous about what event to check for "
    "(not just imprecisely worded), attempt to reframe it as a clear event statement "
    "(e.g. 'X opens', 'X is released', 'X falls below Y') and return valid=false with the reframed version. "
    "(3) If it cannot be salvaged — no identifiable real-world event, impossible to check via web search, "
    "or purely subjective — return valid=false with no reframed field. "
    "Respond ONLY with valid JSON. Include 'reframed' only when rule (2) applies: "
    '{"valid": true/false, "feedback": "one sentence", "reframed": "reframed description"}'
)

GENERATE_SYSTEM_PROMPT = (
    "You generate notification queries from user descriptions. "
    "Given a plain-English description of something to track, produce a precise daily monitoring query. "
    "Respond ONLY with JSON: "
    '{"query_text": "precise question Claude can answer with a web search"}'
)


_MIN_DESC_LEN = 10
_MAX_DESC_LEN = 500
_MIN_WORD_COUNT = 3

_INJECTION_RE = re.compile(
    "|".join([
        # Instruction override attempts
        r"\bignore\s+(all|any|previous|prior|above|your)\s+(instructions?|constraints?|rules?|prompts?|context)\b",
        r"\bforget\s+(all|any|previous|prior|above|your)\s+(instructions?|constraints?|rules?|prompts?|context)\b",
        r"\bdisregard\s+(all|any|previous|prior|above|your)\b",
        r"\boverride\s+(all|any|previous|your|the)\s+(instructions?|constraints?|rules?|prompts?)\b",
        # Role / persona reassignment
        r"\byou\s+are\s+now\b",
        r"\bact\s+as\s+(?:a\s+|an\s+)?[a-z]",
        r"\bpretend\s+(?:you\s+are|to\s+be)\b",
        r"\byour\s+(?:new|real|actual|true)\s+(?:instructions?|role|task|purpose|system|prompt)\b",
        r"\bfrom\s+now\s+on\b",
        # System-prompt injection markers
        r"(?:^|\n)\s*(?:###|\[system\]|\[inst\]|<system>|<\|im_start\|>)\b",
        # JSON output manipulation
        r'"valid"\s*:\s*true',
        r"\bvalid\s*=\s*true\b",
        # Prompt exfiltration
        r"\b(?:print|repeat|output|show|reveal|display)\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions?|rules?)\b",
    ]),
    re.IGNORECASE | re.MULTILINE,
)


def _pre_guard(description: str) -> str | None:
    """Deterministic pre-check before any LLM call. Returns an error string or None if clean."""
    text = description.strip()

    if len(text) < _MIN_DESC_LEN:
        return "Description is too short. Please describe a specific event you want to track."
    if len(text) > _MAX_DESC_LEN:
        return f"Description is too long (maximum {_MAX_DESC_LEN} characters)."
    if len(text.split()) < _MIN_WORD_COUNT:
        return "Please describe the event with at least a few words."

    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text):
        return "Description contains invalid characters."

    non_space = text.replace(" ", "")
    if non_space and max(non_space.count(c) for c in set(non_space)) / len(non_space) > 0.6:
        return "Description doesn't appear to describe a trackable event."

    if _INJECTION_RE.search(text):
        return "Description contains content that cannot be processed."

    return None


class QueryCreate(BaseModel):
    query_text: str = Field(..., min_length=10, max_length=500)


class QueryPatch(BaseModel):
    active: bool | None = None


class DescriptionRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=600)


def _active_count(db: DBSession, user_id: str) -> int:
    return db.query(Query).filter(Query.user_id == user_id, Query.active == True).count()


def _check_and_increment_attempts(db: DBSession, user: User) -> None:
    today = date.today()
    month_start = today.replace(day=1)
    if user.creation_attempts_reset_at != month_start:
        user.creation_attempts_this_month = 0
        user.creation_attempts_reset_at = month_start
    if user.creation_attempts_this_month >= FREE_TIER_CREATION_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Monthly query creation limit ({FREE_TIER_CREATION_LIMIT}) reached.",
        )
    user.creation_attempts_this_month += 1
    db.commit()


def _call_haiku(system: str, user_message: str) -> dict:
    response = anthropic_client.messages.create(
        model=ASSIST_MODEL,
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    text = next((b.text for b in response.content if hasattr(b, "text")), "")
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        text = match.group(1)
    return json.loads(text)


def _get_owned_query(db: DBSession, query_id: str, user: User) -> Query:
    q = db.query(Query).filter(Query.id == query_id, Query.user_id == user.id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Query not found.")
    return q


@router.post("/validate")
@limiter.limit("10/hour")
def validate_description(
    request: Request,
    body: DescriptionRequest,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    rejection = _pre_guard(body.description)
    if rejection:
        return {"valid": False, "feedback": rejection}
    _check_and_increment_attempts(db, user)
    result = _call_haiku(GUARD_SYSTEM_PROMPT, body.description)
    response = {"valid": result.get("valid", False), "feedback": result.get("feedback", "")}
    if "reframed" in result and result["reframed"]:
        response["reframed"] = result["reframed"]
    return response


@router.post("/generate")
@limiter.limit("10/hour")
def generate_query(
    request: Request,
    body: DescriptionRequest,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    rejection = _pre_guard(body.description)
    if rejection:
        raise HTTPException(status_code=422, detail=rejection)
    _check_and_increment_attempts(db, user)
    result = _call_haiku(GENERATE_SYSTEM_PROMPT, body.description)
    query_text = result.get("query_text", "").strip()
    if not query_text:
        raise HTTPException(status_code=500, detail="Generation failed — please try again.")
    return {"query_text": query_text}


@router.get("")
def list_queries(user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    return [
        {
            "id": q.id,
            "query_text": q.query_text,
            "active": q.active,
            "created_at": q.created_at.isoformat(),
        }
        for q in db.query(Query)
        .filter(Query.user_id == user.id)
        .order_by(Query.created_at.desc())
        .all()
    ]


@router.post("", status_code=201)
def create_query(
    body: QueryCreate,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    limit = FREE_TIER_QUERY_LIMIT if user.tier == "free" else 999
    if _active_count(db, user.id) >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Free tier allows {FREE_TIER_QUERY_LIMIT} active queries. Deactivate one or upgrade.",
        )
    q = Query(user_id=user.id, query_text=body.query_text)
    db.add(q)
    db.commit()
    db.refresh(q)
    return {
        "id": q.id,
        "query_text": q.query_text,
        "active": q.active,
        "created_at": q.created_at.isoformat(),
    }


@router.patch("/{query_id}")
def patch_query(
    query_id: str,
    body: QueryPatch,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    q = _get_owned_query(db, query_id, user)
    if body.active is not None:
        q.active = body.active
    db.commit()
    db.refresh(q)
    return {
        "id": q.id,
        "query_text": q.query_text,
        "active": q.active,
        "created_at": q.created_at.isoformat(),
    }


@router.delete("/{query_id}", status_code=204)
def delete_query(
    query_id: str,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    q = _get_owned_query(db, query_id, user)
    db.delete(q)
    db.commit()


@router.get("/{query_id}/history")
def get_history(
    query_id: str,
    page: int = QueryParam(default=1, ge=1),
    page_size: int = QueryParam(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    _get_owned_query(db, query_id, user)
    total = (
        db.query(NotificationLog)
        .filter(NotificationLog.query_id == query_id)
        .count()
    )
    logs = (
        db.query(NotificationLog)
        .filter(NotificationLog.query_id == query_id)
        .order_by(NotificationLog.checked_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": log.id,
                "checked_at": log.checked_at.isoformat(),
                "answer": log.answer,
                "reason": log.reason,
                "sources": json.loads(log.sources) if log.sources else [],
                "email_sent": log.email_sent,
            }
            for log in logs
        ],
    }
