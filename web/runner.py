import json
import logging
import os
import sys
import time
from datetime import date

import anthropic
import resend

from web.database import SessionLocal
from web.models import NotificationLog, Query, User

from core import (
    MODEL,
    has_search_failure,
    log_search_blocks,
    make_batch_request,
    make_message_params,
    parse_response,
)

logger = logging.getLogger(__name__)

# Batch mode: all queries are submitted in a single API call rather than sequentially.
# This costs 50% less (Anthropic batch pricing) and is faster for large query sets —
# the whole run is one submit + poll cycle instead of N blocking API calls.
# Tradeoff: results are not available until the full batch finishes (up to 24h per
# Anthropic SLA, usually much faster). Default off for simplicity; enable on the
# hosted version where cost at scale matters.
USE_BATCH = os.environ.get("NOTIFAI_USE_BATCH", "false").lower() == "true"
BATCH_TIMEOUT_MINUTES = int(os.environ.get("NOTIFAI_BATCH_TIMEOUT_MINUTES", "60"))


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        logger.error("missing_env_var name=%s", name)
        sys.exit(1)
    return value


def get_session():
    return SessionLocal()


def run_checks():
    db = get_session()
    try:
        _run(db)
    finally:
        db.close()


def _run(db):
    client = anthropic.Anthropic()
    resend.api_key = _require_env("RESEND_API_KEY")
    from_email = _require_env("RESEND_FROM_EMAIL")
    today = date.today().isoformat()

    # Only run queries for users who have credits remaining.
    # Users at 0 credits are skipped until they purchase more.
    queries = (
        db.query(Query)
        .join(User, Query.user_id == User.id)
        .filter(Query.active == True, User.query_credits > 0)
        .all()
    )

    if not queries:
        logger.info("runner_start no_active_queries")
        return

    if USE_BATCH and queries:
        _run_batch(client, db, queries, today, from_email)
    else:
        _run_sequential(client, db, queries, today, from_email)


def _handle_result(db, q, user, result_dict, from_email):
    """Process a single query result: send email if YES, auto-deactivate, log.

    Called only when the API returned a valid parsed response — not on errors.
    Deducts 1 credit per call (system faults return early before reaching here).
    """
    # Deduct 1 credit for this execution.
    user.query_credits = max(0, user.query_credits - 1)
    db.commit()
    logger.info("credit_deducted query_id=%s user=%s credits_remaining=%d", q.id, user.email, user.query_credits)

    answer = result_dict.get("answer", "NO")
    reason = result_dict.get("reason", "")
    sources = result_dict.get("sources", [])
    email_sent = False
    label = q.query_text[:40]

    if answer == "YES":
        notify_to = user.notify_email or user.email
        source_lines = "\n".join(f"  - {s}" for s in sources)
        app_base_url = os.environ.get("APP_BASE_URL", "")
        body = (
            f"Query:   {q.query_text}\n"
            f"Answer:  YES\n"
            f"Reason:  {reason}\n"
            f"\nSources:\n{source_lines}\n"
            f"\n✓ This query has been completed and is no longer being checked daily.\n"
            f"If you think this is a mistake, you can re-enable it from your dashboard"
            + (f":\n{app_base_url}/dashboard.html" if app_base_url else ".")
        )
        try:
            resend.Emails.send({
                "from": from_email,
                "to": [notify_to],
                "subject": f"[Notifier] {q.query_text[:80]}",
                "text": body,
            })
            email_sent = True
            logger.info("email_sent query_id=%s to=%s", q.id, notify_to)
            # Auto-deactivate: query answered YES, no further daily checks needed.
            # completed=True distinguishes this from a manual pause (active=False only).
            q.active = False
            q.completed = True
            db.commit()
        except Exception as e:
            logger.error("email_error query_id=%s exc=%s", q.id, e)
    else:
        logger.info("result query_id=%s answer=NO label=%.40r", q.id, label)

    log = NotificationLog(
        query_id=q.id,
        user_id=user.id,
        answer=answer,
        reason=reason,
        sources=json.dumps(sources),
        email_sent=email_sent,
    )
    db.add(log)
    db.commit()


def _run_sequential(client, db, queries, today, from_email):
    logger.info("runner_start query_count=%d mode=sequential", len(queries))
    for q in queries:
        user = db.query(User).filter(User.id == q.user_id).first()
        if not user:
            logger.warning("user_not_found query_id=%s user_id=%s", q.id, q.user_id)
            continue

        logger.info("query_check query_id=%s user=%s query=%.60s", q.id, user.email, q.query_text)

        block_types = ""
        try:
            logger.info("api_call model=%s query_id=%s", MODEL, q.id)
            response = client.messages.create(**make_message_params(q.query_text, today))
            block_types = ",".join(getattr(b, "type", "?") for b in response.content)
            logger.info(
                "api_response query_id=%s stop=%s blocks=%s in_tokens=%d out_tokens=%d",
                q.id, response.stop_reason, block_types,
                response.usage.input_tokens, response.usage.output_tokens,
            )
        except Exception as e:
            logger.error("api_error query_id=%s exc=%s", q.id, e)
            continue

        log_search_blocks(response.content, str(q.id))

        # Skip on web search failure (execution error or rate limit) — no credit deduction.
        failure = has_search_failure(response.content)
        if failure:
            logger.warning("search_failure query_id=%s content=%.200s", q.id, failure)
            continue

        text_blocks = [b.text for b in response.content if hasattr(b, "text")]
        text_block = text_blocks[-1] if text_blocks else None
        if text_block is None:
            logger.warning("no_text_block query_id=%s blocks=%s", q.id, block_types)
            continue

        try:
            result = parse_response(text_block)
        except json.JSONDecodeError:
            logger.warning("parse_error query_id=%s raw=%.200s", q.id, text_block)
            continue

        _handle_result(db, q, user, result, from_email)
    logger.info("runner_done mode=sequential query_count=%d", len(queries))


def _run_batch(client, db, queries, today, from_email):
    logger.info("runner_start query_count=%d mode=batch", len(queries))
    # Build a user lookup up front to avoid per-query DB calls
    user_ids = {q.user_id for q in queries}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()}

    # Filter out queries whose user no longer exists
    valid = [(q, users[q.user_id]) for q in queries if q.user_id in users]
    if not valid:
        return

    requests = [
        make_batch_request(str(q.id), q.query_text, today)
        for q, _ in valid
    ]

    batch = client.messages.batches.create(requests=requests)
    logger.info("batch_submitted batch_id=%s count=%d", batch.id, len(valid))

    deadline = time.time() + BATCH_TIMEOUT_MINUTES * 60
    while time.time() < deadline:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        logger.info("batch_poll batch_id=%s status=%s", batch.id, batch.processing_status)
        time.sleep(30)
    else:
        logger.error("batch_timeout batch_id=%s timeout_minutes=%d", batch.id, BATCH_TIMEOUT_MINUTES)
        return

    query_by_id = {str(q.id): q for q, _ in valid}
    user_by_query_id = {str(q.id): u for q, u in valid}

    for result in client.messages.batches.results(batch.id):
        q = query_by_id.get(result.custom_id)
        user = user_by_query_id.get(result.custom_id)
        if q is None or user is None:
            continue

        if result.result.type != "succeeded":
            logger.error("batch_result_error custom_id=%s result_type=%s", result.custom_id, result.result.type)
            continue

        response = result.result.message
        block_types = ",".join(getattr(b, "type", "?") for b in response.content)
        logger.info(
            "api_response query_id=%s stop=%s blocks=%s in_tokens=%d out_tokens=%d",
            result.custom_id, response.stop_reason, block_types,
            response.usage.input_tokens, response.usage.output_tokens,
        )

        log_search_blocks(response.content, result.custom_id)

        # Skip on web search failure (execution error or rate limit) — no credit deduction.
        failure = has_search_failure(response.content)
        if failure:
            logger.warning("search_failure query_id=%s content=%.200s", result.custom_id, failure)
            continue

        text_blocks = [b.text for b in response.content if hasattr(b, "text")]
        text_block = text_blocks[-1] if text_blocks else None
        if text_block is None:
            logger.warning("no_text_block query_id=%s blocks=%s", result.custom_id, block_types)
            continue

        try:
            parsed = parse_response(text_block)
        except json.JSONDecodeError:
            logger.warning("parse_error query_id=%s raw=%.200s", result.custom_id, text_block)
            continue

        _handle_result(db, q, user, parsed, from_email)


if __name__ == "__main__":
    run_checks()
