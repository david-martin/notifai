# "Notify me when…" UX — Design Spec

**Date:** 2026-05-24  
**Status:** Approved  

---

## Overview

This change makes "Notify me when…" the single governing language for every query in notifai — from how it is stored, to how Claude is asked about it, to how it appears in the UI and in notification emails. The goal is a consistent, human-friendly experience: users always see their intent phrased as a plain English completion of the sentence "Notify me when…", never as an internal search question.

---

## Core Tenet

> **"Notify me when…" is the language.**  
> Every query is stored and displayed as a present-tense event statement that completes the sentence "Notify me when…". The UI, emails, CLI, and runner all frame queries in this form. Never store or display a query as a question.

This is added to `CLAUDE.md` as a core tenet.

---

## The `query_text` Format — Cornerstone

`query_text` (the single stored field in DB and YAML) is a **lowercase present-tense event statement**:

```
the artemis mission lands on the moon
Python 4.0 is officially released
a European country legislates a four-day working week
lab-grown meat goes on sale in a European supermarket
```

It reads naturally after "Notify me when…". It does **not** end with a question mark. It does **not** start with "Has" or "Did".

### Migration note

Existing rows in the DB and entries in `queries.yaml` use the old question form. These are updated as part of this change (two example entries in YAML; live DB rows are a one-time manual migration or left to expire naturally as users add new queries in the correct format).

---

## Changes by File

### 1. `CLAUDE.md`

Add to the Core Tenets table:

| Tenet | Detail |
|---|---|
| "Notify me when…" is the language | Every query is stored and displayed as a present-tense event statement. The UI, emails, CLI, and runner all frame queries as "Notify me when [event]". Never store or display a query as a question. |

Add a reference section documenting where `query_text` surfaces and in what form.

---

### 2. LLM prompt — query generation (Haiku)

**Files:** `web/routers/queries.py` (`COMBINED_SYSTEM_PROMPT`), `assist.py` (`SYSTEM_PROMPT`)

The generation instruction changes from:

> "generate a precise daily monitoring query (e.g. 'Has Python 4.0 been officially released as of today?')"

To:

> "generate a present-tense event statement that completes the sentence 'Notify me when…' (e.g. 'Python 4.0 is officially released', 'the artemis mission lands on the moon'). Use lowercase for the first word. No question marks."

The returned `query_text` field must be in this form.

---

### 3. Runner prompt — event check framing

**Files:** `check.py` (`_make_message_params`), `web/runner.py` (`_make_message_params`), `web/routers/queries.py` (`run_query_now`)

The user message sent to Claude changes from:

```
Today is {today}. Search the web and answer: {query_text}
```

To:

```
Today is {today}. Search the web and determine whether this event has occurred:
"{query_text}"
```

The system prompt (`SYSTEM_PROMPT` / `_RUNNER_SYSTEM_PROMPT`) changes from:

> "For each question, make exactly one web search, then determine if the described condition is met."

To:

> "For each event, make exactly one web search, then determine if the event has occurred."

This framing is cleaner for fact-checking a statement than answering a question.

---

### 4. Email — notification format

**Files:** `check.py` (`format_email_body`, `_run_sequential`, `_run_batch`), `web/runner.py` (`_handle_result`), `web/routers/queries.py` (`run_query_now`)

**Subject:**
```
[notifai] it happened — {query_text[:80]}
```

**Body (hosted runner, YES):**
```
You asked to be notified when:
{query_text}

It happened!
{reason}

Sources:
  - url1
  - url2

✓ This query is now complete and will no longer run daily.
  Re-enable it from your dashboard: {app_url}/dashboard.html
```

**Body (self-hosted, YES):**
```
You asked to be notified when:
{query_text}

It happened!
{reason}

Sources:
  - url1
  - url2
```

**Body (`notify_on_no: true`, NO):**
```
You asked to be notified when:
{query_text}

Not yet.
{reason}

Sources:
  - url1
  - url2
```

---

### 5. `dashboard.html` — query cards

Each query card currently shows `q.query_text` raw in a `.query-text` div. Change to render:

```
Notify me when… {query_text}
```

The "Notify me when…" portion is styled with the existing `--accent` colour and a lighter weight to visually separate the prefix from the statement. Example:

```
[toggle]  Notify me when…
          the artemis mission lands on the moon
          [Active]  [Run now]  [Remove]
```

The prefix can be rendered as a `<span class="notify-prefix-inline">` inside `.query-text`.

### 5b. `dashboard.html` — Step 2 create preview

The generated query preview (step 2) shows:

```
We'll check daily:
Notify me when… {query_text}
```

Replace the current plain `previewQuery` text node with a two-part render.

---

### 6. `history.html` — query selector dropdown

Each `<option>` in the `#querySelect` dropdown prefixes `query_text`:

```
Notify me when… the artemis mission lands on the moon
```

Log entries themselves (reason, sources, date) are unchanged — they are Claude's response prose, not the query.

---

### 7. `index.html` — landing page examples

Change the `.examples-label` from `"For example"` to `"Notify me when…"`.

Each example item already renders with `→` prefix. No further change needed to the example items themselves.

---

### 8. `examples.js`

All examples must read naturally after "Notify me when…". Audit and lowercase the first character of any example that starts with a capital article or generic word (e.g. `"A European country…"` → `"a European country…"`).

Current list is already in good statement form. Minor lowercase pass only.

---

### 9. `queries.yaml` — self-hosted examples

Update two existing entries to statement form:

```yaml
- id: python-4-release
  description: Python 4.0 release
  query: Python 4.0 is officially released
  active: true

- id: mars-crewed-landing
  description: First crewed Mars landing
  query: the first crewed mission to Mars successfully lands on the Martian surface
  active: true
```

---

### 10. `assist.py` — CLI display

Update the `run()` function to display the proposed query with the prefix:

```
Proposed query:
  id:          python-4-release
  description: Python 4.0 release
  Notify me when… Python 4.0 is officially released
```

The interactive prompt also changes from:

```
What do you want to track? Describe it in plain English:
```

To:

```
Notify me when…
(describe the event in plain English — the AI will reword it for you)
```

---

## Where queries appear — full map

| Location | Format shown |
|---|---|
| `dashboard.html` query cards | `Notify me when… {query_text}` |
| `dashboard.html` step 2 preview | `Notify me when… {query_text}` |
| `history.html` dropdown | `Notify me when… {query_text}` |
| `index.html` examples section | label "Notify me when…" + statement examples |
| Email subject (YES) | `[notifai] it happened — {query_text}` |
| Email body (YES / NO) | `You asked to be notified when:\n{query_text}` |
| `assist.py` CLI display | `Notify me when… {query_text}` |
| `queries.yaml` `query:` field | plain statement (no prefix — it's the raw value) |
| DB `query_text` column | plain statement (no prefix — it's the raw value) |
| Runner API call to Claude | `"…determine whether this event has occurred: '{query_text}'"` |

---

## Out of scope

- DB migration script for existing rows (existing users see old-format queries; new queries use the new format automatically)
- Any change to the credit/billing system
- Any change to routing, auth, or rate limiting
- New notification channels

---

## Success criteria

1. A user who creates a new query sees "Notify me when…" at every step of the flow, in every list view, and in every email.
2. The stored `query_text` is a statement that reads naturally after "Notify me when…".
3. Claude's runner prompt uses event-check framing and produces correct YES/NO results.
4. `CLAUDE.md` documents this as a core tenet with a complete reference map.
5. `examples.js`, `queries.yaml`, and `assist.py` all reflect the new format.
