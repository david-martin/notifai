import json
import os
import sys
import time
from datetime import date

import anthropic
import resend
import yaml

from core import make_batch_request, make_message_params, parse_response

# parse_response is the canonical name; keep the old name as an alias so
# any external code or tests that imported parse_claude_response still work.
parse_claude_response = parse_response

# Configuration — all values read from environment variables with sensible defaults.
# Secrets (RESEND_API_KEY, NOTIFY_EMAIL, RESEND_FROM_EMAIL) have no default and the
# script will exit with a clear error if they are not set.

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


def format_email_body(query: str, answer: str, reason: str, sources: list) -> str:
    source_lines = "\n".join(f"  - {s}" for s in sources)
    label = "It happened!" if answer == "YES" else "Not yet."
    return (
        f"You asked to be notified when:\n"
        f"{query}\n"
        f"\n"
        f"{label}\n"
        f"{reason}\n"
        f"\n"
        f"Sources:\n"
        f"{source_lines}"
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
        response = client.messages.create(**make_message_params(q["query"], today))

        text_blocks = [b.text for b in response.content if hasattr(b, "text")]
        text_block = text_blocks[-1] if text_blocks else None
        if text_block is None:
            print(f"  [{q['id']}] No text block in response, skipping.")
            continue

        try:
            result = parse_response(text_block)
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
                    "subject": f"[notifai] it happened — {q['query'][:80]}",
                    "text": body,
                }
            )
            print(f"  [{q['id']}] {answer} — email sent.")
        else:
            print(f"  [{q['id']}] NO — skipping.")


def _run_batch(client, queries, today, notify_email, from_email):
    requests = [
        make_batch_request(q["id"], q["query"], today)
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
            parsed = parse_response(text_block)
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
                "subject": f"[notifai] it happened — {q['query'][:80]}",
                "text": body,
            })
            print(f"  [{q['id']}] {answer} — email sent.")
        else:
            print(f"  [{q['id']}] NO — skipping.")


if __name__ == "__main__":
    run()
