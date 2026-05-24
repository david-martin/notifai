# Design: Extract `core.py` — Single Source for Claude API Logic

**Date:** 2026-05-24  
**Status:** Approved

---

## Problem

Claude API interaction — message construction, response parsing, and search failure detection — is duplicated across three files:

| Thing | `check.py` | `web/runner.py` | `web/routers/queries.py` |
|---|---|---|---|
| `SYSTEM_PROMPT` (runner) | ✅ | ✅ | ✅ (as `_RUNNER_SYSTEM_PROMPT`) |
| `_make_message_params` | ✅ | ✅ | ❌ inline in `run_query_now` |
| `parse_response` | ✅ | ✅ | ✅ (as `_parse_runner_result`) |
| `_log_search_blocks` | ❌ missing | ✅ | ✅ |
| `_has_search_failure` | ❌ missing | ✅ | ✅ |
| `MODEL` + `CACHE_TTL` config | ✅ | ✅ | ⚠️ re-read inline |

This has already caused an active bug: `check.py` uses `web_search_20260209` +
`code_execution_20260120`, which CLAUDE.md explicitly forbids. The divergence happened
because the logic is copied, not shared. When `web/runner.py` was updated, `check.py`
was not.

The structural disconnect — standalone scripts use YAML and env vars; the web backend
uses SQLAlchemy and HTTP — is real and intentional. The Claude API layer is not. It
should be identical everywhere.

---

## Decision

**Option A: Thin `core.py` extraction.**

Extract all stateless Claude API logic into a single `notifai/core.py` module. Every
file that talks to Claude imports from it. Files that do not import from `core.py` do
not touch Claude. This is the rule.

The execution loops and result handling remain in their respective files — they differ
legitimately (credit deduction, DB logging, HTTP error responses vs. print/email). The
Claude API contract does not.

---

## Architecture

```
notifai/
├── core.py                    ← NEW: all Claude API logic lives here
├── check.py                   ← imports from core; handles YAML + email
├── assist.py                  ← imports ASSIST_MODEL from core only
└── web/
    ├── runner.py              ← imports from core; handles DB + credits
    └── routers/
        └── queries.py         ← imports from core; handles HTTP + DB
```

`core.py` is purely functional and stateless. It has no knowledge of YAML,
SQLAlchemy, HTTP, or credits. Config (model names, cache TTL) is read from the
environment once at import time.

---

## `core.py` Contents

### Constants (read from env at import)

| Name | Env var | Default |
|---|---|---|
| `MODEL` | `NOTIFAI_MODEL` | `claude-sonnet-4-6` |
| `CACHE_TTL` | `NOTIFAI_CACHE_TTL` | `"1h"` (≥3600s) or `"5m"` |
| `ASSIST_MODEL` | `NOTIFAI_ASSIST_MODEL` | `claude-haiku-4-5-20251001` |
| `RUNNER_SYSTEM_PROMPT` | — | The canonical runner system prompt string |

### Functions

```python
def make_message_params(query_text: str, today: str) -> dict:
    """Build the complete messages.create() kwargs for a runner query.

    Uses web_search_20250305 with max_uses=1. Includes system prompt cache control.
    """

def make_batch_request(custom_id: str, query_text: str, today: str) -> BatchRequest:
    """Wrap make_message_params as a BatchRequest for the Messages Batch API."""

def parse_response(text: str) -> dict:
    """Strip markdown fences (if any) and JSON-parse a Claude response.

    Raises json.JSONDecodeError on malformed input — callers decide how to handle it.
    """

def log_search_blocks(content_blocks, query_id: str) -> None:
    """Log web search result details for each web_search_tool_result block.

    Uses the standard library logger under the name 'core'.
    """

def has_search_failure(content_blocks) -> str | None:
    """Return a failure description if the response contains a web search failure.

    Returns None if search succeeded (or no search block present).
    Handles both web_search_tool_result and legacy tool_result block patterns.
    """
```

---

## What Stays Local

### `check.py`
- YAML loading (`load_queries`)
- `_run_sequential`, `_run_batch` — execution loops over YAML queries
- `format_email_body`, email sending via Resend
- `_require_env` — sys.exit on missing secrets

### `assist.py`
- Interactive prompting flow
- Standalone system prompt (different format: `{id, description, query}`)
- YAML read/write for appending queries
- Imports `ASSIST_MODEL` from `core` (model name only — no API call logic)

### `web/runner.py`
- DB queries (join Query + User, filter active + credits > 0)
- `_handle_result` — credit deduction, `NotificationLog` write, auto-deactivate on YES
- `_run_sequential`, `_run_batch` — execution loops over DB query objects

### `web/routers/queries.py`
- `COMBINED_SYSTEM_PROMPT` — web-specific validate+generate prompt (different format)
- `_pre_guard` — deterministic input validation before any LLM call
- `_call_haiku` — wraps `anthropic_client.messages.create()` for the assist flow
- HTTP request/response, rate limiting, Pydantic models
- Imports `MODEL`, `CACHE_TTL`, `RUNNER_SYSTEM_PROMPT`, `parse_response`,
  `log_search_blocks`, `has_search_failure`, `make_message_params` from `core`

---

## Fixes Included in This Pass

1. **`check.py` tool corrected.** `web_search_20260209` + `code_execution_20260120`
   replaced with `web_search_20250305`. This is a direct consequence of having a single
   source of truth — `make_message_params` in `core.py` always uses the right tool.

2. **`web/routers/queries.py` stops re-reading env vars inline.** `run_query_now`
   currently re-reads `NOTIFAI_MODEL` and `NOTIFAI_CACHE_TTL` mid-request. After this
   change it uses `core.MODEL` and `core.CACHE_TTL`.

3. **Duplicate functions removed.** `_parse_runner_result`, `_RUNNER_SYSTEM_PROMPT`,
   `_log_search_blocks`, `_has_search_failure` in `queries.py` are deleted. Their
   equivalents in `runner.py` are deleted. Both point to `core`.

---

## CLAUDE.md Tenet

The following tenet will be added to the Core tenets table:

> **Single source for Claude API logic** — All message construction, response parsing,
> and search failure detection lives in `core.py`. Scripts and web handlers are thin
> callers that handle their own data access and result persistence. No Claude API logic
> outside `core.py`.

---

## Testing

Existing tests cover the behaviour end-to-end. No new test files are needed — this is a
pure refactor (behaviour unchanged, duplication eliminated). The test suite passing after
the change is the acceptance criterion.

The `check.py` tool fix (`web_search_20260209` → `web_search_20250305`) is a bug fix,
not a behaviour change from the user's perspective. If any test mocks the old tool name,
it should be updated to match.

---

## Out of Scope

- Unifying the execution loops (`_run_sequential`, `_run_batch`) — they differ
  legitimately and unifying them would require an abstraction that obscures real
  differences (credit deduction, DB logging, HTTP vs. print).
- Changing the `assist.py` system prompt to match the web validate+generate format —
  the standalone tool intentionally produces `{id, description, query}` which is needed
  for YAML authoring.
- Any other refactoring not directly related to eliminating the Claude API duplication.
