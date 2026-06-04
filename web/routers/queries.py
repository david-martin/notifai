import json
import logging
import os
import re
import time
from datetime import date, datetime, timezone

import anthropic
import resend
from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam, Request

resend.api_key = os.environ.get("RESEND_API_KEY", "")
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session as DBSession

from web.auth import get_current_user
from web.database import get_db
from web.limiter import limiter
from web.models import NotificationLog, Query, User
from web.notifications import notify_if_low_balance

from core import (
    ASSIST_MODEL,
    COMBINED_SYSTEM_PROMPT,
    MODEL,
    advance_interval,
    format_email_body,
    has_search_failure,
    INTERVAL_DELTAS,
    log_search_blocks,
    make_message_params,
    parse_response,
)
from web.metrics import (
    claude_api_calls_total,
    email_sends_total,
    query_executions_total,
    query_execution_duration_seconds,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/queries", tags=["queries"])

anthropic_client = anthropic.Anthropic()

VALID_INTERVALS = {"1d", "1w", "1mo"}

# Combined validate+generate prompt is owned by core.py and imported above.
# VALID_INTERVALS stays here — it is HTTP validation, not shared logic.


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
    check_interval: str = Field(default="1d")

    @field_validator("check_interval")
    @classmethod
    def validate_interval(cls, v: str) -> str:
        if v not in VALID_INTERVALS:
            raise ValueError(f"check_interval must be one of {sorted(VALID_INTERVALS)}")
        return v


class QueryPatch(BaseModel):
    active: bool | None = None
    check_interval: str | None = None


class DescriptionRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=600)




def _call_haiku(system: str, user_message: str) -> dict:
    logger.info("haiku_call model=%s desc=%.60s", ASSIST_MODEL, user_message)
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
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("haiku_parse_error raw=%.200s", text)
        raise


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
):
    rejection = _pre_guard(body.description)
    if rejection:
        logger.info("validate_preguard_reject user=%s reason=%.80s", user.email, rejection)
        return {"valid": False, "feedback": rejection}
    result = _call_haiku(COMBINED_SYSTEM_PROMPT, body.description)
    valid = result.get("valid", False)
    logger.info("validate_result user=%s valid=%s feedback=%.80s", user.email, valid, result.get("feedback", ""))
    response: dict = {"valid": valid, "feedback": result.get("feedback", "")}
    if valid:
        query_text = result.get("query_text", "").strip()
        if query_text:
            response["query_text"] = query_text
    elif result.get("reframed"):
        response["reframed"] = result["reframed"]
    return response


def _query_dict(q: Query) -> dict:
    return {
        "id": q.id,
        "query_text": q.query_text,
        "active": q.active,
        "completed": q.completed,
        "created_at": q.created_at.isoformat(),
        "check_interval": q.check_interval,
        "next_check_at": q.next_check_at.isoformat() if q.next_check_at else None,
    }


@router.get("")
def list_queries(user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    return [
        _query_dict(q)
        for q in db.query(Query)
        .filter(Query.user_id == user.id)
        .order_by(Query.created_at.desc())
        .all()
    ]


MAX_ACTIVE_QUERIES = 20


@router.post("", status_code=201)
@limiter.limit("20/hour")
def create_query(
    request: Request,
    body: QueryCreate,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    rejection = _pre_guard(body.query_text)
    if rejection:
        logger.info("create_query_preguard_reject user=%s reason=%.80s", user.email, rejection)
        raise HTTPException(status_code=422, detail=rejection)
    active_count = (
        db.query(Query).filter(Query.user_id == user.id, Query.active == True).count()
    )
    if user.tier == "free" and active_count >= 1:
        logger.info("create_query_free_tier_cap user=%s active=%d", user.email, active_count)
        raise HTTPException(
            status_code=403,
            detail="Free accounts support 1 active query. Purchase credits to add more.",
        )
    if active_count >= MAX_ACTIVE_QUERIES:
        logger.info("create_query_cap_reached user=%s active=%d", user.email, active_count)
        raise HTTPException(
            status_code=429,
            detail=f"Active query limit reached ({MAX_ACTIVE_QUERIES}). Pause or remove a query first.",
        )
    q = Query(user_id=user.id, query_text=body.query_text, check_interval=body.check_interval)
    db.add(q)
    db.commit()
    db.refresh(q)
    return _query_dict(q)


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
    if body.check_interval is not None:
        if body.check_interval not in VALID_INTERVALS:
            raise HTTPException(status_code=422, detail=f"check_interval must be one of {sorted(VALID_INTERVALS)}")
        q.check_interval = body.check_interval
        # Recalculate next_check_at from the most recent log entry, or NULL if none.
        last_log = (
            db.query(NotificationLog)
            .filter(NotificationLog.query_id == query_id)
            .order_by(NotificationLog.checked_at.desc())
            .first()
        )
        if last_log is not None:
            q.next_check_at = advance_interval(last_log.checked_at, body.check_interval)
        else:
            q.next_check_at = None
    db.commit()
    db.refresh(q)
    return _query_dict(q)


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


@router.post("/{query_id}/run")
@limiter.limit("3/hour")
def run_query_now(
    request: Request,
    query_id: str,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    """Run a single query immediately using the real-time API.

    Always uses the real-time API regardless of NOTIFAI_USE_BATCH.
    Costs 1 credit on successful completion. Returns 402 if the user has no credits.
    System faults (API error, no text block, malformed JSON) do NOT deduct credits.
    Sends an email on YES — same as the scheduled runner.
    Does NOT auto-deactivate the query even if the answer is YES.
    """
    q = _get_owned_query(db, query_id, user)

    if user.query_credits <= 0:
        logger.warning("run_query_no_credits query_id=%s user=%s", query_id, user.email)
        raise HTTPException(
            status_code=402,
            detail="No query credits remaining. Purchase more credits to continue.",
        )

    today = date.today().isoformat()

    logger.info("run_query_start query_id=%s user=%s credits=%d", query_id, user.email, user.query_credits)

    t0 = time.monotonic()

    # Always real-time — never batched. "Run now" is an on-demand check;
    # results must be available immediately, not after a batch polling cycle.
    try:
        logger.info("api_call model=%s query=%.60s", MODEL, q.query_text)
        response = anthropic_client.messages.create(**make_message_params(q.query_text, today))
        block_types = ",".join(getattr(b, "type", "?") for b in response.content)
        logger.info(
            "api_response stop=%s blocks=%s in_tokens=%d out_tokens=%d",
            response.stop_reason,
            block_types,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
    except Exception as exc:
        logger.error("api_error query_id=%s exc=%s", query_id, exc)
        claude_api_calls_total.labels(trigger="manual", outcome="api_error").inc()
        query_executions_total.labels(trigger="manual", answer="error").inc()
        raise HTTPException(status_code=500, detail="API error. Please try again.")

    log_search_blocks(response.content, query_id)

    # Detect web search failures (tool execution error OR rate limit / unavailable).
    # These are system faults — do NOT deduct a credit.
    failure = has_search_failure(response.content)
    if failure:
        logger.warning("search_failure query_id=%s content=%.200s", query_id, failure)
        claude_api_calls_total.labels(trigger="manual", outcome="search_failure").inc()
        query_executions_total.labels(trigger="manual", answer="error").inc()
        raise HTTPException(
            status_code=503,
            detail="Web search unavailable. Please try again in a moment.",
        )

    text_blocks = [b.text for b in response.content if hasattr(b, "text")]
    text_block = text_blocks[-1] if text_blocks else None
    if text_block is None:
        logger.warning("no_text_block query_id=%s blocks=%s", query_id, block_types)
        claude_api_calls_total.labels(trigger="manual", outcome="api_error").inc()
        query_executions_total.labels(trigger="manual", answer="error").inc()
        raise HTTPException(status_code=500, detail="No result from API. Please try again.")

    try:
        result = parse_response(text_block)
    except json.JSONDecodeError:
        logger.warning("parse_error query_id=%s raw=%.200s", query_id, text_block)
        claude_api_calls_total.labels(trigger="manual", outcome="parse_error").inc()
        query_executions_total.labels(trigger="manual", answer="error").inc()
        raise HTTPException(status_code=500, detail="Malformed API response. Please try again.")

    claude_api_calls_total.labels(trigger="manual", outcome="success").inc()
    query_execution_duration_seconds.labels(trigger="manual").observe(time.monotonic() - t0)

    answer = result.get("answer", "NO")
    reason = result.get("reason", "")
    sources = result.get("sources", [])
    email_sent = False

    query_executions_total.labels(trigger="manual", answer=answer).inc()

    # Deduct 1 credit — only reached on a valid answer (tool errors and parse
    # failures return early above without reaching this point).
    user.query_credits = max(0, user.query_credits - 1)
    db.commit()
    logger.info("credit_deducted query_id=%s credits_remaining=%d", query_id, user.query_credits)

    from_email = os.environ.get("RESEND_FROM_EMAIL", "")
    if from_email:
        notify_if_low_balance(user, db, from_email)

    if answer == "YES":
        from_email = os.environ.get("RESEND_FROM_EMAIL", "")
        api_key = os.environ.get("RESEND_API_KEY", "")
        if api_key:
            resend.api_key = api_key
        if from_email and api_key:
            notify_to = user.notify_email or user.email
            body = format_email_body(q.query_text, answer, reason, sources)
            try:
                resend.Emails.send({
                    "from": from_email,
                    "to": [notify_to],
                    "subject": f"[notifai] it happened — {q.query_text[:80]}",
                    "text": body,
                })
                email_sent = True
                email_sends_total.labels(trigger="manual", outcome="success").inc()
                logger.info("email_sent query_id=%s to=%s", query_id, notify_to)
            except Exception as exc:
                logger.error("email_error query_id=%s exc=%s", query_id, exc)
                email_sends_total.labels(trigger="manual", outcome="error").inc()
        # No auto-deactivation: manual "run now" checks do not retire the query.

    logger.info("run_query_done query_id=%s answer=%s email_sent=%s", query_id, answer, email_sent)
    checked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    log = NotificationLog(
        query_id=q.id,
        user_id=user.id,
        answer=answer,
        reason=reason,
        sources=json.dumps(sources),
        email_sent=email_sent,
        checked_at=checked_at,
    )
    db.add(log)
    db.commit()

    return {
        "answer": answer,
        "reason": reason,
        "sources": sources,
        "checked_at": checked_at.isoformat(),
        "email_sent": email_sent,
    }
