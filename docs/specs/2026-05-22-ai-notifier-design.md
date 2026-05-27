# AI Notifier — Design Spec

**Date:** 2026-05-22  
**Status:** Approved  
**Repo:** New private repo (TBD name)

---

## Vision

An AI-powered event notification system. Define natural-language queries — "have oil prices fallen back to pre-war levels?", "have Stray Kids announced a European concert for 2026?" — and receive an email when the answer is yes. Runs daily without any manual intervention and without requiring the user's machine to be on.

---

## Core Tenets

- **Always-on, not local.** Runs on GitHub Actions. No dependency on the user's machine.
- **Stay small.** Regularly question scope. Every addition must earn its place.
- **Steel thread first.** Get e2e working, then iterate toward efficiency.
- **Scripts are the artifact.** Hosting is a thin wrapper around portable Python scripts.
- **Silent on NO.** Only send email when a condition is met. No noise.

---

## What was evaluated and ruled out

- **Claude.ai scheduled tasks** — explored but ruled out. Could only create email drafts, not send. Integration too limited for reliable delivery.
- **Running on user's machine** — ruled out. Core tenet: must not require PC to be on.
- **Existing OSS alternatives** — researched. Nothing exists that does exactly this (natural-language condition → LLM evaluation → notify). Closest commercial analog is SignalHub, but it monitors specific sources rather than evaluating free-form questions.

---

## Architecture

```
queries.yaml
    │
    ▼
check.py  ◄──── GitHub Actions cron (7am UTC daily)
    │
    ├── Claude API (claude-sonnet-4-6, web_search enabled)
    │       └── returns JSON: {answer, reason, sources}
    │
    └── Resend API (email, only on YES)
```

`assist.py` is a local-only CLI tool — not part of the GHA workflow. Runs interactively on the user's machine to add queries.

---

## Repo Structure

```
ai-notifier/
├── check.py              # daily runner — GHA target
├── assist.py             # interactive query configurator
├── queries.yaml          # all configured queries — the only persistent state
├── requirements.txt      # anthropic, resend, pyyaml
├── .github/
│   └── workflows/
│       └── notify.yml    # schedule + manual trigger
└── CLAUDE.md             # vision, tenets, decisions
```

---

## queries.yaml

The single source of truth. No database. Edited directly or via `assist.py`.

```yaml
queries:
  - id: oil-prices
    description: Oil prices back to pre-war levels
    query: Have Brent crude oil prices fallen back below $80/barrel (pre-Russia-Ukraine war levels)?
    active: true

  - id: stray-kids-europe
    description: Stray Kids European concert announced for 2026
    query: Have Stray Kids officially announced any concert or tour dates in Europe for 2026?
    active: true
```

**Fields:**
- `id` — slug, used in logs
- `description` — email subject line
- `query` — sent to Claude verbatim
- `active` — toggle without deleting

---

## check.py

1. Load `queries.yaml`, filter `active: true`
2. For each query:
   - Call Claude API (`claude-sonnet-4-6`) with `web_search` tool
   - System prompt: instructs Claude to act as an event monitor and return JSON only
   - User message: `Today is {date}. Search the web and answer: {query}\nReturn JSON: {"answer": "YES"|"NO", "reason": "one sentence", "sources": ["url1", ...]}`
   - Parse JSON response
   - If `answer == "YES"` → send email via Resend
   - If `answer == "NO"` → skip silently
   - If malformed JSON → log raw response, skip query, continue

**Error handling:**
- API failure → script exits non-zero → GHA run marked failed → visible in repo
- No retries. Runs again tomorrow.

**Email format (plain text):**
```
Subject: [Notifier] {description}

Query:   {query}
Answer:  YES
Reason:  {reason}

Sources:
  - {url1}
  - {url2}
```

---

## assist.py

Local CLI. Run when adding or editing a query.

1. Prompt user for plain-English description of what to track
2. Call Claude API (`claude-haiku-4-5-20251001`) — no web search needed, cheap, fast
3. Claude returns proposed `id`, `description`, and well-formed `query`
4. Show to user: confirm / edit / discard
5. On confirm → append to `queries.yaml`

Editing and deleting queries is done directly in `queries.yaml`. No CRUD CLI needed — readable YAML is the interface.

**Why Haiku here:** Query reformulation is a short-input/short-output task. No retrieval. No reason to pay for Sonnet.

---

## GitHub Actions Workflow

```yaml
name: Daily Notify
on:
  schedule:
    - cron: '0 7 * * *'   # 7am UTC daily
  workflow_dispatch:        # manual trigger for testing
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: python check.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          RESEND_API_KEY: ${{ secrets.RESEND_API_KEY }}
          NOTIFY_EMAIL: ${{ secrets.NOTIFY_EMAIL }}
```

**Secrets (set in GitHub repo settings):**
- `ANTHROPIC_API_KEY`
- `RESEND_API_KEY`
- `NOTIFY_EMAIL`

---

## Portability

GHA is the host, not the product. Moving to any VPS (Hetzner, etc.) means:
1. Copy the repo
2. Set env vars
3. `crontab -e` → `0 7 * * * cd /path/to/repo && python check.py`

~15 minutes of effort. No lock-in.

---

## Explicitly Out of Scope

- Web UI for query management
- Multi-user support
- History / run log storage
- Retry logic
- Per-query model override (iteration, not steel thread)
- Scheduling frequency per query (iteration, not steel thread)
