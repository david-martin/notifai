# Implementation Plan: notifai

## Overview

Build a minimal AI-powered event notification system. Five files, no database, no framework. A daily GitHub Actions cron calls `check.py`, which asks Claude (with web search) whether each configured condition is met, and sends an email via Resend if the answer is YES. A local `assist.py` helps users author queries interactively.

## Architecture Decisions

- **check.py is the only GHA target.** assist.py is purely local — it never runs in CI.
- **queries.yaml is the only persistent state.** No DB, no run history.
- **Email only on YES.** GHA failure (non-zero exit) is the signal for API/config errors.
- **claude-sonnet-4-6 for check.py, claude-haiku-4-5-20251001 for assist.py.** Sonnet needs web search and reasoning; Haiku is cheaper for query reformulation only.
- **Prompt caching** on the system prompt in check.py (repeated across queries in the same run).

## Dependency Graph

```
requirements.txt          (no deps)
queries.yaml              (no deps)
        │
        ├── check.py      (reads queries.yaml; calls Anthropic + Resend)
        │
        └── assist.py     (reads/writes queries.yaml; calls Anthropic)

check.py
        │
        └── .github/workflows/notify.yml  (invokes check.py in CI)
```

Build order: requirements → queries.yaml → check.py → workflow → assist.py

---

## Phase 1: Foundation

### Task 1: Project scaffolding — requirements.txt and queries.yaml

**Description:** Create the two config files that everything else depends on. `requirements.txt` pins the three runtime deps. `queries.yaml` ships two sample queries matching the spec exactly — these double as integration test fixtures.

**Acceptance criteria:**
- [ ] `requirements.txt` lists `anthropic`, `resend`, and `pyyaml` (pinned to compatible versions)
- [ ] `queries.yaml` contains exactly the two sample queries from the spec (`oil-prices`, `stray-kids-europe`), both `active: true`
- [ ] `python -c "import yaml; yaml.safe_load(open('queries.yaml'))"` succeeds

**Verification:**
- [ ] `pip install -r requirements.txt` completes without error
- [ ] YAML parses cleanly with PyYAML

**Dependencies:** None

**Files:**
- `requirements.txt`
- `queries.yaml`

**Estimated scope:** XS

---

## Checkpoint: After Task 1
- [ ] `pip install -r requirements.txt` succeeds
- [ ] queries.yaml parses without error

---

## Phase 2: Core Runner

### Task 2: check.py — full daily runner

**Description:** Implement the entire `check.py` pipeline in one pass: load queries, call Claude with `web_search`, parse the JSON response, send email via Resend on YES, log and continue on malformed JSON, exit non-zero on API failures. This is the steel thread — all external integrations in one cohesive script.

**Acceptance criteria:**
- [ ] Loads `queries.yaml`, filters `active: true`, iterates all active queries
- [ ] Calls `claude-sonnet-4-6` with `web_search` tool enabled and the system prompt from the spec
- [ ] User message format: `Today is {date}. Search the web and answer: {query}\nReturn JSON: {"answer": "YES"|"NO", "reason": "one sentence", "sources": ["url1", ...]}`
- [ ] Parses JSON from Claude's response text
- [ ] On `answer == "YES"`: sends plain-text email via Resend with the exact format from the spec (subject `[Notifier] {description}`, body with Query/Answer/Reason/Sources)
- [ ] On `answer == "NO"`: skips silently
- [ ] On malformed JSON: logs raw response, skips query, continues to next
- [ ] On Anthropic API error: exits non-zero (no try/catch swallowing it)
- [ ] Reads `ANTHROPIC_API_KEY`, `RESEND_API_KEY`, `NOTIFY_EMAIL` from environment
- [ ] System prompt uses prompt caching (`cache_control: {"type": "ephemeral"}`) to reduce cost across multiple queries per run

**Verification:**
- [ ] `ANTHROPIC_API_KEY=... RESEND_API_KEY=... NOTIFY_EMAIL=... python check.py` runs without error
- [ ] With a deliberately YES-triggering query (e.g., "Is today a day of the week?"), email arrives at `NOTIFY_EMAIL`
- [ ] With a NO query, script exits 0 and sends no email
- [ ] Corrupting the JSON parse (mock or manual) logs the raw response and doesn't crash

**Dependencies:** Task 1

**Files:**
- `check.py`

**Estimated scope:** M

---

## Checkpoint: After Task 2 — Steel Thread Complete
- [ ] `python check.py` runs end-to-end locally
- [ ] Email received for a YES condition
- [ ] Script exits 0 on all-NO run
- [ ] **Human review before proceeding to CI**

---

## Phase 3: CI Automation

### Task 3: GitHub Actions workflow — notify.yml

**Description:** Wire `check.py` into GitHub Actions. Daily cron at 7am UTC + manual `workflow_dispatch` trigger. Sets the three secrets as environment variables. No caching of pip deps needed for this project size.

**Acceptance criteria:**
- [ ] Workflow triggers on `schedule: cron '0 7 * * *'` and `workflow_dispatch`
- [ ] Uses `actions/checkout@v4` and `actions/setup-python@v5` with Python 3.12
- [ ] Runs `pip install -r requirements.txt` then `python check.py`
- [ ] Passes `ANTHROPIC_API_KEY`, `RESEND_API_KEY`, `NOTIFY_EMAIL` from GitHub secrets
- [ ] Workflow file is valid YAML (parseable)

**Verification:**
- [ ] Manual trigger via GitHub Actions UI runs successfully
- [ ] GHA run log shows script output

**Dependencies:** Task 2

**Files:**
- `.github/workflows/notify.yml`

**Estimated scope:** XS

---

## Checkpoint: After Task 3 — Always-On Achieved
- [ ] Workflow runs successfully via `workflow_dispatch`
- [ ] Cron will fire daily at 7am UTC
- [ ] **Human review before proceeding to assist.py**

---

## Phase 4: Query Authoring Tool

### Task 4: assist.py — interactive query builder

**Description:** Local-only CLI that takes a plain-English description of what to track, calls `claude-haiku-4-5-20251001` to generate a well-formed `id`, `description`, and `query`, shows the result to the user for confirm/edit/discard, and on confirm appends to `queries.yaml`.

**Acceptance criteria:**
- [ ] Prompts user for a plain-English description (stdin)
- [ ] Calls `claude-haiku-4-5-20251001` — no tools, no web search
- [ ] Claude returns (and script parses) a JSON with `id`, `description`, `query`
- [ ] Displays the proposed entry and prompts: confirm (y), edit (e), discard (n)
- [ ] On confirm: appends the new entry (`active: true`) to `queries.yaml`
- [ ] On edit: allows user to modify each field interactively before saving
- [ ] On discard: exits without writing
- [ ] Does not duplicate an existing `id` (warns and re-prompts for a new id)
- [ ] Reads `ANTHROPIC_API_KEY` from environment

**Verification:**
- [ ] `ANTHROPIC_API_KEY=... python assist.py` runs end-to-end
- [ ] New query appears in `queries.yaml` after confirming
- [ ] Discarding leaves `queries.yaml` unchanged
- [ ] `python check.py` still works after assist.py adds a new query

**Dependencies:** Task 1 (queries.yaml schema)

**Files:**
- `assist.py`

**Estimated scope:** S

---

## Checkpoint: After Task 4 — Complete
- [ ] All five files exist and are functional
- [ ] `python check.py` runs e2e
- [ ] `python assist.py` adds a query and check.py picks it up
- [ ] GHA workflow runs successfully
- [ ] Ready for use

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Claude doesn't return clean JSON | Med | Spec already handles: log raw, skip, continue |
| `web_search` tool not available on account | High | Verify tool availability before implementing; fall back to `brave_search` if needed |
| Resend free tier limits | Low | Only fires on YES — expected to be rare |
| GHA secrets not set | Med | Workflow fails loudly (non-zero exit) — visible in repo |

## Open Questions

- None — spec is fully approved and complete.
