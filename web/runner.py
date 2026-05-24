import json
import os
import re
import sys
import time
from datetime import date

import anthropic
import resend

from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request as BatchRequest

from web.database import SessionLocal
from web.models import NotificationLog, Query, User

SYSTEM_PROMPT = (
    "You are an event monitor. For each question, make exactly one web search, "
    "then determine if the described condition is met. "
    "Respond ONLY with valid JSON in this exact format: "
    '{"answer": "YES" or "NO", "reason": "one sentence explanation", "sources": ["url1", "url2"]}'
)

# Configuration — all values read from environment variables with sensible defaults.
# Secrets (RESEND_API_KEY, RESEND_FROM_EMAIL, ANTHROPIC_API_KEY) have no default and
# the runner will exit with a clear error if they are not set.

MODEL = os.environ.get("NOTIFAI_MODEL", "claude-sonnet-4-6")

# Cache TTL for the system prompt. API accepts "1h" or "5m".
# Env var is integer seconds for readability; converted to string on use.
# 1 hour reduces prompt write costs when many queries run close together.
_cache_ttl_secs = int(os.environ.get("NOTIFAI_CACHE_TTL", "3600"))
CACHE_TTL = "1h" if _cache_ttl_secs >= 3600 else "5m"

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
        print(f"Error: required environment variable {name} is not set.", file=sys.stderr)
        sys.exit(1)
    return value


def get_session():
    return SessionLocal()


def _parse_response(text: str) -> dict:
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        text = match.group(1)
    return json.loads(text)


def _make_message_params(query_text: str, today: str) -> dict:
    return dict(
        model=MODEL,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral", "ttl": CACHE_TTL},
            }
        ],
        tools=[
            {"type": "web_search_20260209", "name": "web_search", "max_uses": 1},
            {"type": "code_execution_20260120", "name": "code_execution"},
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"Today is {today}. Search the web and answer: {query_text}\n"
                    f'Return JSON: {{"answer": "YES"|"NO", "reason": "one sentence", "sources": ["url1", ...]}}'
                ),
            }
        ],
    )


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

    queries = db.query(Query).filter(Query.active == True).all()

    if USE_BATCH and queries:
        _run_batch(client, db, queries, today, from_email)
    else:
        _run_sequential(client, db, queries, today, from_email)


def _handle_result(db, q, user, result_dict, from_email):
    """Process a single query result: send email if YES, auto-deactivate, log."""
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
            print(f"[runner] {label}: YES — email sent to {notify_to}")
            # Auto-deactivate: query answered YES, no further daily checks needed.
            # completed=True distinguishes this from a manual pause (active=False only).
            q.active = False
            q.completed = True
            db.commit()
        except Exception as e:
            print(f"[runner] Email error for {label}: {e}", file=sys.stderr)
    else:
        print(f"[runner] {label}: NO — silent.")

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
    for q in queries:
        user = db.query(User).filter(User.id == q.user_id).first()
        if not user:
            continue

        label = q.query_text[:40]
        print(f"[runner] Checking '{label}' for {user.email}")

        try:
            response = client.messages.create(**_make_message_params(q.query_text, today))
        except Exception as e:
            print(f"[runner] API error for {label}: {e}", file=sys.stderr)
            continue

        text_blocks = [b.text for b in response.content if hasattr(b, "text")]
        text_block = text_blocks[-1] if text_blocks else None
        if text_block is None:
            print(f"[runner] No text block for {label}, skipping.")
            continue

        try:
            result = _parse_response(text_block)
        except json.JSONDecodeError:
            print(f"[runner] Malformed JSON for {label}: {text_block!r}")
            continue

        _handle_result(db, q, user, result, from_email)


def _run_batch(client, db, queries, today, from_email):
    # Build a user lookup up front to avoid per-query DB calls
    user_ids = {q.user_id for q in queries}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()}

    # Filter out queries whose user no longer exists
    valid = [(q, users[q.user_id]) for q in queries if q.user_id in users]
    if not valid:
        return

    requests = [
        BatchRequest(
            custom_id=str(q.id),
            params=MessageCreateParamsNonStreaming(**_make_message_params(q.query_text, today)),
        )
        for q, _ in valid
    ]

    batch = client.messages.batches.create(requests=requests)
    print(f"[runner] Batch {batch.id} submitted ({len(valid)} queries). Polling every 30s...")

    deadline = time.time() + BATCH_TIMEOUT_MINUTES * 60
    while time.time() < deadline:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        print(f"[runner] Batch {batch.id}: {batch.processing_status} — waiting...")
        time.sleep(30)
    else:
        print(
            f"[runner] Batch {batch.id} did not finish within {BATCH_TIMEOUT_MINUTES} minutes.",
            file=sys.stderr,
        )
        return

    query_by_id = {str(q.id): q for q, _ in valid}
    user_by_query_id = {str(q.id): u for q, u in valid}

    for result in client.messages.batches.results(batch.id):
        q = query_by_id.get(result.custom_id)
        user = user_by_query_id.get(result.custom_id)
        if q is None or user is None:
            continue

        if result.result.type != "succeeded":
            print(f"[runner] Batch result error for {result.custom_id}: {result.result.type}")
            continue

        response = result.result.message
        text_blocks = [b.text for b in response.content if hasattr(b, "text")]
        text_block = text_blocks[-1] if text_blocks else None
        if text_block is None:
            print(f"[runner] No text block for {result.custom_id}, skipping.")
            continue

        try:
            parsed = _parse_response(text_block)
        except json.JSONDecodeError:
            print(f"[runner] Malformed JSON for {result.custom_id}: {text_block!r}")
            continue

        _handle_result(db, q, user, parsed, from_email)


if __name__ == "__main__":
    run_checks()
