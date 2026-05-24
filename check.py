import json
import os
import re
import sys
import time
from datetime import date

import anthropic
import resend
import yaml

from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request as BatchRequest

SYSTEM_PROMPT = (
    "You are an event monitor. For each question, make exactly one web search, "
    "then determine if the described condition is met. "
    "Respond ONLY with valid JSON in this exact format: "
    '{"answer": "YES" or "NO", "reason": "one sentence explanation", "sources": ["url1", "url2"]}'
)

# Configuration — all values read from environment variables with sensible defaults.
# Secrets (RESEND_API_KEY, NOTIFY_EMAIL, RESEND_FROM_EMAIL) have no default and the
# script will exit with a clear error if they are not set.

MODEL = os.environ.get("NOTIFAI_MODEL", "claude-sonnet-4-6")

# Cache TTL for the system prompt. API accepts "1h" or "5m".
# Env var is integer seconds for readability; converted to string on use.
# 1 hour reduces prompt write costs when many queries run close together.
_cache_ttl_secs = int(os.environ.get("NOTIFAI_CACHE_TTL", "3600"))
CACHE_TTL = "1h" if _cache_ttl_secs >= 3600 else "5m"

QUERIES_PATH = os.environ.get("NOTIFAI_QUERIES_PATH", "queries.yaml")

# Batch mode: all queries are submitted in a single API call rather than sequentially.
# This costs 50% less (Anthropic batch pricing) and is faster for large query sets —
# the whole run is one submit + poll cycle instead of N blocking API calls.
# Tradeoff: results are not available until the full batch finishes (up to 24h per
# Anthropic SLA, usually much faster). Default off for simplicity.
USE_BATCH = os.environ.get("NOTIFAI_USE_BATCH", "false").lower() == "true"
BATCH_TIMEOUT_MINUTES = int(os.environ.get("NOTIFAI_BATCH_TIMEOUT_MINUTES", "60"))


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"Error: required environment variable {name} is not set.", file=sys.stderr)
        sys.exit(1)
    return value


def load_queries(path: str) -> list:
    with open(path) as f:
        data = yaml.safe_load(f)
    return [q for q in data["queries"] if q.get("active")]


def parse_claude_response(text: str) -> dict:
    text = text.strip()
    # Strip markdown code block if present
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        text = match.group(1)
    return json.loads(text)


def format_email_body(query: str, answer: str, reason: str, sources: list) -> str:
    source_lines = "\n".join(f"  - {s}" for s in sources)
    return (
        f"Query:   {query}\n"
        f"Answer:  {answer}\n"
        f"Reason:  {reason}\n"
        f"\n"
        f"Sources:\n"
        f"{source_lines}"
    )


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


def run():
    queries = load_queries(QUERIES_PATH)
    if not queries:
        print("No active queries.")
        return

    client = anthropic.Anthropic()
    resend.api_key = _require_env("RESEND_API_KEY")
    notify_email = _require_env("NOTIFY_EMAIL")
    from_email = _require_env("RESEND_FROM_EMAIL")
    today = date.today().isoformat()

    if USE_BATCH:
        _run_batch(client, queries, today, notify_email, from_email)
    else:
        _run_sequential(client, queries, today, notify_email, from_email)


def _run_sequential(client, queries, today, notify_email, from_email):
    for q in queries:
        print(f"Checking: {q['id']}")
        response = client.messages.create(**_make_message_params(q["query"], today))

        text_blocks = [b.text for b in response.content if hasattr(b, "text")]
        text_block = text_blocks[-1] if text_blocks else None
        if text_block is None:
            print(f"  [{q['id']}] No text block in response, skipping.")
            continue

        try:
            result = parse_claude_response(text_block)
        except json.JSONDecodeError:
            print(f"  [{q['id']}] Malformed JSON response: {text_block!r}")
            continue

        answer = result.get("answer", "NO")
        should_email = answer == "YES" or q.get("notify_on_no", False)

        if should_email:
            body = format_email_body(q["query"], answer, result["reason"], result.get("sources", []))
            resend.Emails.send(
                {
                    "from": from_email,
                    "to": [notify_email],
                    "subject": f"[Notifier] {q['description']}",
                    "text": body,
                }
            )
            print(f"  [{q['id']}] {answer} — email sent.")
        else:
            print(f"  [{q['id']}] NO — skipping.")


def _run_batch(client, queries, today, notify_email, from_email):
    requests = [
        BatchRequest(
            custom_id=q["id"],
            params=MessageCreateParamsNonStreaming(**_make_message_params(q["query"], today)),
        )
        for q in queries
    ]

    batch = client.messages.batches.create(requests=requests)
    print(f"Batch {batch.id} submitted ({len(queries)} queries). Polling every 30s...")

    deadline = time.time() + BATCH_TIMEOUT_MINUTES * 60
    while time.time() < deadline:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        print(f"  Status: {batch.processing_status} — waiting...")
        time.sleep(30)
    else:
        print(
            f"Batch {batch.id} did not finish within {BATCH_TIMEOUT_MINUTES} minutes.",
            file=sys.stderr,
        )
        return

    query_by_id = {q["id"]: q for q in queries}

    for result in client.messages.batches.results(batch.id):
        q = query_by_id.get(result.custom_id)
        if q is None:
            continue

        if result.result.type != "succeeded":
            print(f"  [{result.custom_id}] Error: {result.result.type}")
            continue

        response = result.result.message
        text_blocks = [b.text for b in response.content if hasattr(b, "text")]
        text_block = text_blocks[-1] if text_blocks else None
        if text_block is None:
            print(f"  [{result.custom_id}] No text block in response, skipping.")
            continue

        try:
            parsed = parse_claude_response(text_block)
        except json.JSONDecodeError:
            print(f"  [{result.custom_id}] Malformed JSON: {text_block!r}")
            continue

        answer = parsed.get("answer", "NO")
        should_email = answer == "YES" or q.get("notify_on_no", False)

        if should_email:
            body = format_email_body(q["query"], answer, parsed["reason"], parsed.get("sources", []))
            resend.Emails.send({
                "from": from_email,
                "to": [notify_email],
                "subject": f"[Notifier] {q['description']}",
                "text": body,
            })
            print(f"  [{q['id']}] {answer} — email sent.")
        else:
            print(f"  [{q['id']}] NO — skipping.")


if __name__ == "__main__":
    run()
