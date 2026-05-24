import json
import os
import re
import sys
from datetime import date

import anthropic
import resend
import yaml

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

# Extended cache TTL reduces prompt write costs when many queries run close together.
# Default 1 hour — adjust lower if cache write cost is a concern.
CACHE_TTL = int(os.environ.get("NOTIFAI_CACHE_TTL", "3600"))

QUERIES_PATH = os.environ.get("NOTIFAI_QUERIES_PATH", "queries.yaml")


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

    for q in queries:
        print(f"Checking: {q['id']}")
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
                        f"Today is {today}. Search the web and answer: {q['query']}\n"
                        f'Return JSON: {{"answer": "YES"|"NO", "reason": "one sentence", "sources": ["url1", ...]}}'
                    ),
                }
            ],
        )

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


if __name__ == "__main__":
    run()
