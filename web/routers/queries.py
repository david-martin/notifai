import json
import logging
import os
import re
from datetime import date, datetime, timezone

import anthropic
import resend
from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from web.auth import get_current_user
from web.database import get_db
from web.limiter import limiter
from web.models import NotificationLog, Query, User

from core import (
    ASSIST_MODEL,
    MODEL,
    has_search_failure,
    log_search_blocks,
    make_message_params,
    parse_response,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/queries", tags=["queries"])

anthropic_client = anthropic.Anthropic()

# Combined validate+generate: if the description is valid, we include the
# generated query_text in the same response instead of making a second API call.
# This saves one round trip for valid descriptions (~90% of cases) while keeping
# the same two-call maximum for invalid ones (reframe attempt → retry → generate).
# The attempt counter is incremented once per call to /validate.
COMBINED_SYSTEM_PROMPT = (
    "You evaluate and convert user-submitted event descriptions for a notification service. "
    "A valid description is one where a web search could plausibly return a yes/no answer — "
    "something real-world, specific enough to check, and that could become true in the future. "
    "Be permissive: if the intent is clear and a web search could answer it, it is valid. "
    "Do not reject descriptions just because they could be phrased more precisely. "
    "Rules: "
    "(1) If the intent is clear and the event is checkable via web search: return valid=true and "
    "generate query_text as a present-tense event statement that completes the sentence "
    "'Notify me when…' — e.g. 'Python 4.0 is officially released', "
    "'the artemis mission lands on the moon'. "
    "Use lowercase for the first word. No question marks. "
    "(2) If phrased as a question, or genuinely ambiguous about what event to check for "
    "(not just imprecisely worded): return valid=false with a reframed description. "
    "(3) If it cannot be salvaged (no identifiable real-world event, impossible to check via web "
    "search, or purely subjective): return valid=false with no reframed field. "
    "Respond ONLY with valid JSON. "
    "Include 'query_text' only when valid=true, 'reframed' only when rule (2) applies: "
    '{"valid": true, "feedback": "one sentence", "query_text": "event statement"} '
    'or {"valid": false, "feedback": "one sentence", "reframed": "clearer description"} '
    'or {"valid": false, "feedback": "one sentence"}'
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


@router.post("", status_code=201)
def create_query(
    body: QueryCreate,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    q = Query(user_id=user.id, query_text=body.query_text)
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
        raise HTTPException(status_code=500, detail="API error. Please try again.")

    log_search_blocks(response.content, query_id)

    # Detect web search failures (tool execution error OR rate limit / unavailable).
    # These are system faults — do NOT deduct a credit.
    failure = has_search_failure(response.content)
    if failure:
        logger.warning("search_failure query_id=%s content=%.200s", query_id, failure)
        raise HTTPException(
            status_code=503,
            detail="Web search unavailable. Please try again in a moment.",
        )

    text_blocks = [b.text for b in response.content if hasattr(b, "text")]
    text_block = text_blocks[-1] if text_blocks else None
    if text_block is None:
        logger.warning("no_text_block query_id=%s blocks=%s", query_id, block_types)
        raise HTTPException(status_code=500, detail="No result from API. Please try again.")

    try:
        result = parse_response(text_block)
    except json.JSONDecodeError:
        logger.warning("parse_error query_id=%s raw=%.200s", query_id, text_block)
        raise HTTPException(status_code=500, detail="Malformed API response. Please try again.")

    answer = result.get("answer", "NO")
    reason = result.get("reason", "")
    sources = result.get("sources", [])
    email_sent = False

    # Deduct 1 credit — only reached on a valid answer (tool errors and parse
    # failures return early above without reaching this point).
    user.query_credits = max(0, user.query_credits - 1)
    db.commit()
    logger.info("credit_deducted query_id=%s credits_remaining=%d", query_id, user.query_credits)

    if answer == "YES":
        from_email = os.environ.get("RESEND_FROM_EMAIL", "")
        api_key = os.environ.get("RESEND_API_KEY", "")
        if from_email and api_key:
            notify_to = user.notify_email or user.email
            source_lines = "\n".join(f"  - {s}" for s in sources)
            body = (
                f"You asked to be notified when:\n"
                f"{q.query_text}\n"
                f"\n"
                f"It happened!\n"
                f"{reason}\n"
                f"\n"
                f"Sources:\n"
                f"{source_lines}"
            )
            try:
                resend.api_key = api_key
                resend.Emails.send({
                    "from": from_email,
                    "to": [notify_to],
                    "subject": f"[notifai] it happened — {q.query_text[:80]}",
                    "text": body,
                })
                email_sent = True
                logger.info("email_sent query_id=%s to=%s", query_id, notify_to)
            except Exception as exc:
                logger.error("email_error query_id=%s exc=%s", query_id, exc)
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
