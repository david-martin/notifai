import json
import os
import re
import sys
from datetime import date

import anthropic
import resend

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

# Extended cache TTL reduces prompt write costs when many queries run close together.
# Default 1 hour — adjust lower if cache write cost is a concern.
CACHE_TTL = int(os.environ.get("NOTIFAI_CACHE_TTL", "3600"))


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

    queries = (
        db.query(Query)
        .filter(Query.active == True)
        .all()
    )

    for q in queries:
        user = db.query(User).filter(User.id == q.user_id).first()
        if not user:
            continue

        label = q.query_text[:40]
        print(f"[runner] Checking '{label}' for {user.email}")

        try:
            response = client.messages.create(
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
                            f"Today is {today}. Search the web and answer: {q.query_text}\n"
                            f'Return JSON: {{"answer": "YES"|"NO", "reason": "one sentence", "sources": ["url1", ...]}}'
                        ),
                    }
                ],
            )
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

        answer = result.get("answer", "NO")
        reason = result.get("reason", "")
        sources = result.get("sources", [])
        email_sent = False

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
                q.active = False
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


if __name__ == "__main__":
    run_checks()
