# notifai — Claude Code Guide

## What this is

An AI-powered event notification system. Define natural-language queries — "has Half-Life 3 been announced?", "has the PS5 had a price drop?" — and receive an email when the answer is YES. Runs on a schedule without any manual intervention.

The core project is self-hosted and open-source. A hosted version also exists — deployment and ops details for that live in a separate private repo.

---

## Core tenets

| Tenet | Detail |
|---|---|
| Stay small | Regularly question scope. Every addition must earn its place. |
| Steel thread first | Get e2e working, then iterate toward efficiency. |
| Scripts are the artifact | The Python core is the product. The UI and hosting are wrappers. |
| Silent on NO | Only notify when a condition is met. No noise. |
| Vanilla where possible | No framework bloat. Browser standards, plain Python, readable code. |
| Single source for shared logic | All shared logic lives in `core.py` — Claude API calls, response parsing, system prompts, interval arithmetic, email body construction. If two or more callsites need it, it belongs here. Callers handle their own data access and side effects. `core.py` is pure and stateless — no web imports, no YAML, no SQLAlchemy. |
| "Notify me when…" is the language | Every query is stored and displayed as a present-tense event statement that completes the sentence "Notify me when…". The UI, emails, CLI, and runner all frame queries in this form. The stored `query_text` never contains a question mark. |

---

## Self-hosted

Clone the repo, edit `queries.yaml`, run `check.py` with your own Anthropic and Resend API keys. Schedule however you like — a VPS cron, your own machine, any CI runner. The `assist.py` CLI helps you author queries interactively. No account needed, no subscription.

**Who it's for:** developers who want full control and are comfortable managing API keys and a scheduler.

---

## How the daily check works

- Loads all active queries from `queries.yaml`
- For each query, asks Claude to search the web and answer: is this condition met today?
- If **YES** → sends a plain-text email with the reason and sources
- If **NO** → silent. No noise.

`notify_on_no: true` is available in the self-hosted core for testing.

---

## Query authoring (self-hosted)

```bash
ANTHROPIC_API_KEY=... RESEND_API_KEY=... NOTIFY_EMAIL=... RESEND_FROM_EMAIL=... python assist.py
```

Prompts you for what to track, validates it, generates a precise daily check query, and appends it to `queries.yaml`.

---

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API key | *(required)* |
| `RESEND_API_KEY` | Resend email key | *(required)* |
| `NOTIFY_EMAIL` | Address to send notifications to | *(required)* |
| `RESEND_FROM_EMAIL` | Sender address (e.g. `notifai <you@yourdomain.com>`) | *(required)* |
| `NOTIFAI_MODEL` | Claude model for daily checks | `claude-sonnet-4-6` |
| `NOTIFAI_ASSIST_MODEL` | Claude model for query authoring | `claude-haiku-4-5-20251001` |
| `NOTIFAI_CACHE_TTL` | System prompt cache TTL in seconds | `3600` |
| `NOTIFAI_QUERIES_PATH` | Path to queries YAML file | `queries.yaml` |
| `NOTIFAI_USE_BATCH` | Use Claude Batch API (see below) | `false` |
| `NOTIFAI_BATCH_TIMEOUT_MINUTES` | Max wait for batch completion | `60` |

---

## Query validation rules

The guard model evaluates whether a description is checkable. It is intentionally **permissive** — if the intent is clear and a web search could answer it, it passes. It only rejects things that genuinely can't be checked:

- Phrased as a question → suggest reframe (one retry allowed)
- Genuinely ambiguous about what event to check → suggest reframe
- No identifiable real-world event, or purely subjective → hard reject

It does **not** reject things just because they could be phrased more precisely.

Hard limit: max 1 LLM call per query creation (combined validate + generate in one call). No looping.

---

## Decisions already made

| Area | Decision |
|---|---|
| Query runner model | `claude-sonnet-4-6` with `web_search_20250305` (one search, `max_uses=1`) |
| Query assistant model | `claude-haiku-4-5-20251001` (validation + generation — cheap, fast) |
| Email | Resend |
| Self-hosted config | `queries.yaml` — the only persistent state, no database |
| Self-hosted scheduler | Any cron (VPS, local machine, CI) |
| Web stack (SaaS) | FastAPI + SQLite + Alembic + Uvicorn |
| Frontend | Vanilla JS — no frameworks, no build step |
| Backend language | Python |
| Notifications | Email only (for now) |
| Query storage | `query_text` only — no slug or label generated |

---

## Query surfaces — where `query_text` appears

`query_text` is a present-tense event statement, e.g. `"Half-Life 3 is officially announced by Valve"`.
It is stored without any prefix. Every surface that displays it adds "Notify me when…" at render time.

| Surface | Rendered form |
|---|---|
| `dashboard.html` query cards | `Notify me when… {query_text}` |
| `dashboard.html` step 2 create preview | `Notify me when… {query_text}` |
| `history.html` query selector dropdown | `Notify me when… {query_text}` |
| `index.html` examples section | label "Notify me when…" above each example |
| Email subject (YES) | `[notifai] it happened — {query_text}` |
| Email body (YES) | `You asked to be notified when:\n{query_text}\n\nIt happened!\n{reason}\n\nSources:…` |
| Email body (NO, notify_on_no) | `You asked to be notified when:\n{query_text}\n\nNot yet.\n{reason}\n\nSources:…` |
| `assist.py` CLI display | `Notify me when… {query_text}` |
| Runner message to Claude | `…determine whether this event has occurred:\n"{query_text}"` |
| `queries.yaml` `query:` field | plain statement, no prefix |
| DB `query_text` column | plain statement, no prefix |

---

## Repo structure

```
notifai/
├── core.py                         # single source for all Claude API logic
├── check.py                        # self-hosted daily runner
├── assist.py                       # self-hosted interactive query builder
├── queries.yaml                    # self-hosted query config (only persistent state)
├── requirements.txt                # self-hosted dependencies
├── web/                            # hosted SaaS backend
│   ├── main.py                     # FastAPI app
│   ├── models.py                   # SQLAlchemy models
│   ├── database.py                 # DB session setup
│   ├── auth.py                     # session/user helpers
│   ├── limiter.py                  # slowapi rate limiter
│   ├── runner.py                   # multi-tenant daily runner
│   ├── requirements.txt            # SaaS dependencies
│   ├── alembic.ini
│   ├── alembic/versions/           # DB migrations
│   └── routers/
│       ├── auth.py                 # magic link auth
│       ├── queries.py              # query CRUD + validation/generation
│       └── billing.py             # Stripe checkout + webhook
├── web/static/                     # frontend HTML/JS
│   ├── index.html                  # landing + sign-in
│   ├── dashboard.html              # query management
│   ├── history.html                # notification history
│   ├── account.html                # account + billing
│   └── examples.js                 # shared example queries
├── docs/specs/                     # design specs
├── tests/                          # pytest suite
├── tasks/                          # planning docs
└── CLAUDE.md                       # this file
```

---

## Explicitly out of scope (do not add without discussion)

**Self-hosted core:**
- Retry logic
- Per-query model override
- Run history / log storage

**Hosted SaaS (not yet, may change):**
- Non-email notification channels (SMS, Slack, webhooks)
- Team / shared query lists
- Public query templates or marketplace
- Mobile app

---

## Web search tool — do not change without reading this

The runner uses `web_search_20250305` with `max_uses=1`. This is intentional.

**Do NOT switch to `web_search_20260209`** — it is a "deep research" compound tool that
internally spins up a `code_execution` sandbox, fetches pages inside it, and delivers
results back as `code_execution_tool_result` blocks. It routinely hits Anthropic's internal
execution budget on queries (~60 s, 1500+ output tokens) and then reports a "tool limit
error" to the model, which answers NO with a false failure reason. Confirmed bad in production.

**Do NOT add `code_execution_20260120`** to the tools list. It was removed because the 2026
web search tool was triggering 15+ code execution cycles per query. With the 2025 tool it
is not needed.

The 2025 tool (`web_search_20250305`) returns search results directly, is fast (~5 s), and
gives Claude clean text to reason from — exactly right for a YES/NO event check.

---

## Logging

`web/logging_config.py` — centralised logging with per-request trace IDs.

Every log line from the app looks like:
```
[INFO    ] queries      [a3f2b1c0] run_query_start query_id=... user=... credits=12
```

The `[a3f2b1c0]` token is the request trace ID set by the middleware in `main.py`. All log
lines for one HTTP request share the same ID. Use it to trace a full request end-to-end:

```bash
sudo journalctl -u notifai-web | grep a3f2b1c0
```

Key events logged in `run_query_now`:
- `run_query_start` — query_id, user, credits
- `api_call` — model, query (truncated)
- `api_response` — stop reason, block types, token counts
- `web_search_results` — count of results returned + up to 5 titles/URLs
- `web_search_error` — error_code if the search tool itself failed (no credit deducted)
- `search_failure` — failure description (triggers 503, no credit deducted)
- `credit_deducted` — credits_remaining
- `run_query_done` — answer, email_sent

---

## References

- Original design spec (self-hosted): `docs/specs/2026-05-22-ai-notifier-design.md`
- Hosted deployment & ops (logs, services, deploy files): maintained in a separate private repository
