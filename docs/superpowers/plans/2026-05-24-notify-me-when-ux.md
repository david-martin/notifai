# "Notify me when…" UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Propagate the "Notify me when…" language through every surface where a query appears — storage format, LLM prompts, email, web UI, and CLI.

**Architecture:** `query_text` is stored as a lowercase present-tense event statement ("the artemis mission lands on the moon"). The UI prepends "Notify me when…" at display time. The runner reframes its prompt from "answer this question" to "determine whether this event has occurred". Emails open with "You asked to be notified when:" and close with "It happened!" or "Not yet."

**Tech Stack:** Python (check.py, assist.py, web/runner.py, web/routers/queries.py), vanilla JS (dashboard.html, history.html, index.html, examples.js), pytest

---

## File Map

| File | What changes |
|---|---|
| `CLAUDE.md` | Add core tenet + query surfaces reference map |
| `queries.yaml` | Update 2 example entries to statement form |
| `check.py` | SYSTEM_PROMPT → event language; `_make_message_params` → event-check framing; `format_email_body` → new structure; email subject |
| `web/runner.py` | SYSTEM_PROMPT → event language; `_make_message_params` → event-check framing; `_handle_result` → new email body + subject |
| `web/routers/queries.py` | `COMBINED_SYSTEM_PROMPT` → statement-form generation; `_RUNNER_SYSTEM_PROMPT` → event language; `run_query_now` → message framing + email body + subject |
| `assist.py` | `SYSTEM_PROMPT` → statement-form generation; CLI prompt text; card display |
| `web/static/examples.js` | Lowercase first word of non-proper-noun entries |
| `web/static/index.html` | `.examples-label` → "Notify me when…" |
| `web/static/dashboard.html` | Query cards + step 2 preview → "Notify me when…" prefix |
| `web/static/history.html` | Query selector dropdown → prefix each option |
| `tests/test_check.py` | Update `TestFormatEmailBody`; add `TestMakeMessageParams` + `TestSystemPrompt` |
| `tests/test_assist.py` | Update fixture query strings to statement form (cosmetic); add `TestAssistSystemPrompt` |

---

## Task 1: Update CLAUDE.md — core tenet + query surfaces map

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add "Notify me when…" to the Core tenets table**

Open `CLAUDE.md`. Find the `## Core tenets` section. Add a new row to the table:

```markdown
| "Notify me when…" is the language | Every query is stored and displayed as a present-tense event statement that completes the sentence "Notify me when…". The UI, emails, CLI, and runner all frame queries in this form. The stored `query_text` never contains a question mark. |
```

- [ ] **Step 2: Add query surfaces reference section**

Append a new section before `## Repo structure`:

```markdown
## Query surfaces — where `query_text` appears

`query_text` is a present-tense event statement, e.g. `"the artemis mission lands on the moon"`.
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
```

- [ ] **Step 3: Commit**

```bash
cd notifai
git add CLAUDE.md
git commit -m "docs(CLAUDE.md): add notify-me-when core tenet and query surfaces map"
```

---

## Task 2: Update `queries.yaml` — statement-form examples

**Files:**
- Modify: `queries.yaml`

- [ ] **Step 1: Replace question-form entries with statement form**

Replace the entire file content:

```yaml
queries:
  - id: python-4-release
    description: Python 4.0 release
    query: Python 4.0 is officially released
    active: true

  - id: mars-crewed-landing
    description: First crewed Mars landing
    query: the first crewed mission to Mars successfully lands on the Martian surface
    active: true
```

Note: `description` is a short human label (used in the self-hosted email subject). `query` is the stored statement.

- [ ] **Step 2: Commit**

```bash
git add queries.yaml
git commit -m "chore(queries.yaml): update example queries to statement form"
```

---

## Task 3: Update `check.py` — event framing, email format (TDD)

**Files:**
- Modify: `check.py`
- Modify: `tests/test_check.py`

### 3a — Write failing tests first

- [ ] **Step 1: Replace `TestFormatEmailBody` and add two new test classes in `tests/test_check.py`**

Replace the existing `TestFormatEmailBody` class and add imports at the top:

```python
# At the top of the file, the import line already reads:
# from check import load_queries, parse_claude_response, format_email_body
# Add _make_message_params and SYSTEM_PROMPT to that import:
from check import load_queries, parse_claude_response, format_email_body, _make_message_params, SYSTEM_PROMPT
```

Then replace the entire `TestFormatEmailBody` class:

```python
class TestFormatEmailBody:
    def test_includes_query_reason_and_sources(self):
        body = format_email_body(
            query="oil prices fall to pre-war levels",
            answer="YES",
            reason="Prices have dropped significantly.",
            sources=["http://source1.com", "http://source2.com"],
        )
        assert "oil prices fall to pre-war levels" in body
        assert "Prices have dropped significantly." in body
        assert "http://source1.com" in body
        assert "http://source2.com" in body

    def test_yes_uses_notify_me_when_language(self):
        body = format_email_body(
            query="oil prices fall to pre-war levels",
            answer="YES",
            reason="Prices dropped.",
            sources=["http://source1.com"],
        )
        assert "You asked to be notified when:" in body
        assert "oil prices fall to pre-war levels" in body
        assert "It happened!" in body
        assert "Sources:" in body
        assert "http://source1.com" in body

    def test_no_uses_not_yet_language(self):
        body = format_email_body(
            query="oil prices fall to pre-war levels",
            answer="NO",
            reason="Prices remain high.",
            sources=["http://source1.com"],
        )
        assert "You asked to be notified when:" in body
        assert "oil prices fall to pre-war levels" in body
        assert "Not yet." in body
        assert "Sources:" in body

    def test_handles_empty_sources(self):
        body = format_email_body(
            query="something happens", answer="NO", reason="Nothing yet.", sources=[]
        )
        assert "Sources:" in body
        assert "You asked to be notified when:" in body


class TestMakeMessageParams:
    def test_uses_event_check_framing(self):
        params = _make_message_params("the artemis mission lands on the moon", "2026-05-24")
        content = params["messages"][0]["content"]
        assert "determine whether this event has occurred" in content
        assert '"the artemis mission lands on the moon"' in content
        assert "2026-05-24" in content

    def test_does_not_use_question_framing(self):
        params = _make_message_params("the artemis mission lands on the moon", "2026-05-24")
        content = params["messages"][0]["content"]
        assert "Search the web and answer:" not in content


class TestSystemPrompt:
    def test_uses_event_language(self):
        assert "event" in SYSTEM_PROMPT
        assert "occurred" in SYSTEM_PROMPT

    def test_does_not_use_question_language(self):
        assert "question" not in SYSTEM_PROMPT
```

- [ ] **Step 2: Run tests — expect failures**

```bash
cd notifai
python -m pytest tests/test_check.py::TestFormatEmailBody tests/test_check.py::TestMakeMessageParams tests/test_check.py::TestSystemPrompt -v
```

Expected: multiple FAILs — `"You asked to be notified when:"` not in body, `"It happened!"` not in body, `"determine whether this event has occurred"` not in content, `"question"` in SYSTEM_PROMPT, etc.

### 3b — Implement the changes in `check.py`

- [ ] **Step 3: Update `SYSTEM_PROMPT`**

Replace:
```python
SYSTEM_PROMPT = (
    "You are an event monitor. For each question, make exactly one web search, "
    "then determine if the described condition is met. "
    "Respond ONLY with valid JSON in this exact format: "
    '{"answer": "YES" or "NO", "reason": "one sentence explanation", "sources": ["url1", "url2"]}'
)
```

With:
```python
SYSTEM_PROMPT = (
    "You are an event monitor. For each event, make exactly one web search, "
    "then determine if the event has occurred. "
    "Respond ONLY with valid JSON in this exact format: "
    '{"answer": "YES" or "NO", "reason": "one sentence explanation", "sources": ["url1", "url2"]}'
)
```

- [ ] **Step 4: Update `_make_message_params` user message**

Replace the `messages` list inside `_make_message_params`:
```python
        messages=[
            {
                "role": "user",
                "content": (
                    f"Today is {today}. Search the web and answer: {query_text}\n"
                    f'Return JSON: {{"answer": "YES"|"NO", "reason": "one sentence", "sources": ["url1", ...]}}'
                ),
            }
        ],
```

With:
```python
        messages=[
            {
                "role": "user",
                "content": (
                    f"Today is {today}. Search the web and determine whether this event has occurred:\n"
                    f'"{query_text}"\n'
                    f'Return JSON: {{"answer": "YES"|"NO", "reason": "one sentence", "sources": ["url1", ...]}}'
                ),
            }
        ],
```

- [ ] **Step 5: Update `format_email_body`**

Replace:
```python
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
```

With:
```python
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
```

- [ ] **Step 6: Update the email subject in `_run_sequential`**

Find the `resend.Emails.send(` call inside `_run_sequential`. Replace:
```python
                    "subject": f"[Notifier] {q['description']}",
```
With:
```python
                    "subject": f"[notifai] it happened — {q['query'][:80]}",
```

- [ ] **Step 7: Update the email subject in `_run_batch`**

Find the `resend.Emails.send(` call inside `_run_batch`. Replace:
```python
                "subject": f"[Notifier] {q['description']}",
```
With:
```python
                "subject": f"[notifai] it happened — {q['query'][:80]}",
```

- [ ] **Step 8: Run tests — expect all pass**

```bash
python -m pytest tests/test_check.py -v
```

Expected: all tests PASS.

- [ ] **Step 9: Commit**

```bash
git add check.py tests/test_check.py
git commit -m "feat(check.py): notify-me-when email format and event-check runner framing"
```

---

## Task 4: Update `web/runner.py` — event framing + email format

**Files:**
- Modify: `web/runner.py`

(No dedicated test file for the web runner — the email body and message params are inline. The `SYSTEM_PROMPT` and `_make_message_params` are parallel to `check.py`; the format is verified by the analogous tests in Task 3.)

- [ ] **Step 1: Update `SYSTEM_PROMPT`**

Replace:
```python
SYSTEM_PROMPT = (
    "You are an event monitor. For each question, make exactly one web search, "
    "then determine if the described condition is met. "
    "Respond ONLY with valid JSON in this exact format: "
    '{"answer": "YES" or "NO", "reason": "one sentence explanation", "sources": ["url1", "url2"]}'
)
```

With:
```python
SYSTEM_PROMPT = (
    "You are an event monitor. For each event, make exactly one web search, "
    "then determine if the event has occurred. "
    "Respond ONLY with valid JSON in this exact format: "
    '{"answer": "YES" or "NO", "reason": "one sentence explanation", "sources": ["url1", "url2"]}'
)
```

- [ ] **Step 2: Update `_make_message_params` user message**

Replace the `messages` list inside `_make_message_params`:
```python
        messages=[
            {
                "role": "user",
                "content": (
                    f"Today is {today}. Search the web and answer: {query_text}\n"
                    f'Return JSON: {{"answer": "YES"|"NO", "reason": "one sentence", "sources": ["url1", ...]}}'
                ),
            }
        ],
```

With:
```python
        messages=[
            {
                "role": "user",
                "content": (
                    f"Today is {today}. Search the web and determine whether this event has occurred:\n"
                    f'"{query_text}"\n'
                    f'Return JSON: {{"answer": "YES"|"NO", "reason": "one sentence", "sources": ["url1", ...]}}'
                ),
            }
        ],
```

- [ ] **Step 3: Update `_handle_result` — email body and subject**

Find the `if answer == "YES":` block inside `_handle_result`. Replace the entire body-building and `resend.Emails.send` section:

```python
    if answer == "YES":
        notify_to = user.notify_email or user.email
        source_lines = "\n".join(f"  - {s}" for s in sources)
        app_base_url = os.environ.get("APP_BASE_URL", "")
        dashboard_link = f"{app_base_url}/dashboard.html" if app_base_url else "your dashboard"
        body = (
            f"You asked to be notified when:\n"
            f"{q.query_text}\n"
            f"\n"
            f"It happened!\n"
            f"{reason}\n"
            f"\n"
            f"Sources:\n"
            f"{source_lines}\n"
            f"\n"
            f"✓ This query is now complete and will no longer run daily.\n"
            f"  Re-enable it from {dashboard_link}."
        )
        try:
            resend.Emails.send({
                "from": from_email,
                "to": [notify_to],
                "subject": f"[notifai] it happened — {q.query_text[:80]}",
                "text": body,
            })
            email_sent = True
            logger.info("email_sent query_id=%s to=%s", q.id, notify_to)
            q.active = False
            q.completed = True
            db.commit()
        except Exception as e:
            logger.error("email_error query_id=%s exc=%s", q.id, e)
```

- [ ] **Step 4: Run existing tests to verify nothing broken**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS (runner.py has no direct unit tests; the functional shape is confirmed by manual/integration testing).

- [ ] **Step 5: Commit**

```bash
git add web/runner.py
git commit -m "feat(runner.py): notify-me-when email format and event-check runner framing"
```

---

## Task 5: Update `web/routers/queries.py` — generation prompt, runner prompt, email format

**Files:**
- Modify: `web/routers/queries.py`

- [ ] **Step 1: Update `COMBINED_SYSTEM_PROMPT` — generation instruction**

The prompt currently says:
```python
    "(1) If the intent is clear and the event is checkable via web search: return valid=true and "
    "generate a precise daily monitoring query as query_text "
    "(e.g. 'Has Python 4.0 been officially released as of today?'). "
```

Replace those three lines with:
```python
    "(1) If the intent is clear and the event is checkable via web search: return valid=true and "
    "generate query_text as a present-tense event statement that completes the sentence "
    "'Notify me when…' — e.g. 'Python 4.0 is officially released', "
    "'the artemis mission lands on the moon'. "
    "Use lowercase for the first word. No question marks. "
```

The full updated `COMBINED_SYSTEM_PROMPT` should be:
```python
COMBINED_SYSTEM_PROMPT = (
    "You evaluate and convert user-submitted event descriptions for a notification service. "
    "A valid description is one where a web search could plausibly return a yes/no answer — "
    "something real-world, specific enough to check, and that could become true in the future. "
    "Be permissive: if the intent is clear and a web search could answer it, it is valid. "
    "Do not reject descriptions just because they could be phrased more precisely. "
    "Rules: "
    "(1) If the intent is clear and the event is checkable via web search: return valid=true and "
    "generate query_text as a present-tense event statement that completes the sentence "
    "'Notify me when…' — e.g. 'Python 4.0 is officially released', "
    "'the artemis mission lands on the moon'. "
    "Use lowercase for the first word. No question marks. "
    "(2) If phrased as a question, or genuinely ambiguous about what event to check for "
    "(not just imprecisely worded): return valid=false with a reframed description. "
    "(3) If it cannot be salvaged (no identifiable real-world event, impossible to check via web "
    "search, or purely subjective): return valid=false with no reframed field. "
    "Respond ONLY with valid JSON. "
    "Include 'query_text' only when valid=true, 'reframed' only when rule (2) applies: "
    '{"valid": true, "feedback": "one sentence", "query_text": "event statement"} '
    'or {"valid": false, "feedback": "one sentence", "reframed": "clearer description"} '
    'or {"valid": false, "feedback": "one sentence"}'
)
```

- [ ] **Step 2: Update `_RUNNER_SYSTEM_PROMPT`**

Replace:
```python
_RUNNER_SYSTEM_PROMPT = (
    "You are an event monitor. For each question, make exactly one web search, "
    "then determine if the described condition is met. "
    "Respond ONLY with valid JSON in this exact format: "
    '{"answer": "YES" or "NO", "reason": "one sentence explanation", "sources": ["url1", "url2"]}'
)
```

With:
```python
_RUNNER_SYSTEM_PROMPT = (
    "You are an event monitor. For each event, make exactly one web search, "
    "then determine if the event has occurred. "
    "Respond ONLY with valid JSON in this exact format: "
    '{"answer": "YES" or "NO", "reason": "one sentence explanation", "sources": ["url1", "url2"]}'
)
```

- [ ] **Step 3: Update the runner message in `run_query_now`**

Find the `messages` list inside the `anthropic_client.messages.create(` call in `run_query_now`. Replace:
```python
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Today is {today}. Search the web and answer: {q.query_text}\n"
                        f'Return JSON: {{"answer": "YES"|"NO", "reason": "one sentence", "sources": ["url1", ...]}}'
                    ),
                }
            ],
```

With:
```python
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Today is {today}. Search the web and determine whether this event has occurred:\n"
                        f'"{q.query_text}"\n'
                        f'Return JSON: {{"answer": "YES"|"NO", "reason": "one sentence", "sources": ["url1", ...]}}'
                    ),
                }
            ],
```

- [ ] **Step 4: Update the email body and subject in `run_query_now`**

Find the `if answer == "YES":` block in `run_query_now`. Replace the body-building and `resend.Emails.send` call:

```python
    if answer == "YES":
        from_email = os.environ.get("RESEND_FROM_EMAIL", "")
        api_key = os.environ.get("RESEND_API_KEY", "")
        if from_email and api_key:
            notify_to = user.notify_email or user.email
            source_lines = "\n".join(f"  - {s}" for s in sources)
            body = (
                f"You asked to be notified when:\n"
                f"{q.query_text}\n"
                f"\n"
                f"It happened!\n"
                f"{reason}\n"
                f"\n"
                f"Sources:\n"
                f"{source_lines}"
            )
            try:
                resend.api_key = api_key
                resend.Emails.send({
                    "from": from_email,
                    "to": [notify_to],
                    "subject": f"[notifai] it happened — {q.query_text[:80]}",
                    "text": body,
                })
                email_sent = True
                logger.info("email_sent query_id=%s to=%s", query_id, notify_to)
            except Exception as exc:
                logger.error("email_error query_id=%s exc=%s", query_id, exc)
```

- [ ] **Step 5: Write tests for the updated prompts**

Add a new test file `tests/test_queries_router.py`:

```python
"""Tests for web/routers/queries.py prompt content and structure."""
import sys
import os

# Allow importing from the web package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from web.routers.queries import COMBINED_SYSTEM_PROMPT, _RUNNER_SYSTEM_PROMPT


class TestCombinedSystemPrompt:
    def test_instructs_statement_form_generation(self):
        assert "present-tense event statement" in COMBINED_SYSTEM_PROMPT

    def test_includes_notify_me_when_example(self):
        assert "Notify me when" in COMBINED_SYSTEM_PROMPT

    def test_instructs_lowercase_first_word(self):
        assert "lowercase" in COMBINED_SYSTEM_PROMPT

    def test_instructs_no_question_marks(self):
        assert "No question marks" in COMBINED_SYSTEM_PROMPT

    def test_does_not_give_question_form_example(self):
        # Should not show the old "Has Python 4.0 been released?" style example
        assert "Has Python 4.0 been officially released as of today?" not in COMBINED_SYSTEM_PROMPT


class TestRunnerSystemPrompt:
    def test_uses_event_language(self):
        assert "event" in _RUNNER_SYSTEM_PROMPT
        assert "occurred" in _RUNNER_SYSTEM_PROMPT

    def test_does_not_use_question_language(self):
        assert "question" not in _RUNNER_SYSTEM_PROMPT
        assert "condition is met" not in _RUNNER_SYSTEM_PROMPT
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/test_queries_router.py -v
```

Expected: all tests PASS.

- [ ] **Step 7: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add web/routers/queries.py tests/test_queries_router.py
git commit -m "feat(queries.py): statement-form generation prompt, event-check runner, notify-me-when emails"
```

---

## Task 6: Update `assist.py` — statement-form prompt + CLI display

**Files:**
- Modify: `assist.py`
- Modify: `tests/test_assist.py`

### 6a — Write failing tests first

- [ ] **Step 1: Add `TestAssistSystemPrompt` to `tests/test_assist.py`**

Add this import at the top of the file (alongside existing imports):
```python
from assist import parse_proposed_query, check_duplicate_id, append_query, SYSTEM_PROMPT
```

Then add a new test class at the end of the file:
```python
class TestAssistSystemPrompt:
    def test_instructs_statement_form_generation(self):
        assert "present-tense event statement" in SYSTEM_PROMPT
        assert "Notify me when" in SYSTEM_PROMPT

    def test_instructs_lowercase_first_word(self):
        assert "lowercase" in SYSTEM_PROMPT

    def test_does_not_use_question_form_language(self):
        assert "precise question to ask daily" not in SYSTEM_PROMPT
```

Also update existing query strings in `TestParseProposedQuery` and `TestCheckDuplicateId` and `TestAppendQuery` to use statement form (they still parse correctly — this is cosmetic alignment):

In `TestParseProposedQuery.test_parses_valid_json`:
```python
    def test_parses_valid_json(self):
        text = json.dumps({"id": "oil-prices", "description": "Oil prices", "query": "oil prices fall to pre-war levels"})
        result = parse_proposed_query(text)
        assert result["id"] == "oil-prices"
        assert result["description"] == "Oil prices"
        assert result["query"] == "oil prices fall to pre-war levels"
```

In `TestParseProposedQuery.test_handles_json_in_markdown_block`:
```python
    def test_handles_json_in_markdown_block(self):
        inner = json.dumps({"id": "test", "description": "D", "query": "something happens"})
        text = f"```json\n{inner}\n```"
        result = parse_proposed_query(text)
        assert result["id"] == "test"
```

In `TestCheckDuplicateId` fixture data — replace `"query": "Q?"` with `"query": "something happens"` in both tests.

In `TestAppendQuery` fixture data and calls — replace `"query": "Q1?"` / `"query": "Q2?"` with `"query": "something happens"` / `"query": "another thing happens"`.

- [ ] **Step 2: Run tests — expect `TestAssistSystemPrompt` failures**

```bash
python -m pytest tests/test_assist.py::TestAssistSystemPrompt -v
```

Expected: FAIL — `"present-tense event statement"` not in `SYSTEM_PROMPT`.

### 6b — Implement changes in `assist.py`

- [ ] **Step 3: Update `SYSTEM_PROMPT`**

Replace:
```python
SYSTEM_PROMPT = (
    "You are a query assistant. Given a plain-English description of something to track, "
    "generate a well-formed monitoring query. "
    "Respond ONLY with valid JSON in this exact format: "
    '{"id": "kebab-case-slug", "description": "short label", "query": "precise question to ask daily"}'
)
```

With:
```python
SYSTEM_PROMPT = (
    "You are a query assistant. Given a plain-English description of something to track, "
    "generate a well-formed monitoring query. "
    "The query must be a present-tense event statement that completes the sentence 'Notify me when…' — "
    "for example: 'Python 4.0 is officially released', 'the artemis mission lands on the moon'. "
    "Use lowercase for the first word. No question marks. "
    "Respond ONLY with valid JSON in this exact format: "
    '{"id": "kebab-case-slug", "description": "short label", "query": "event statement"}'
)
```

- [ ] **Step 4: Update CLI prompt text in `run()`**

Replace:
```python
    print("What do you want to track? Describe it in plain English:")
    description = input("> ").strip()
```

With:
```python
    print("Notify me when…")
    print("(describe the event in plain English — the AI will reword it for you)")
    description = input("> ").strip()
```

- [ ] **Step 5: Update CLI display of proposed query in `run()`**

Replace:
```python
        print("\nProposed query:")
        print(f"  id:          {entry['id']}")
        print(f"  description: {entry['description']}")
        print(f"  query:       {entry['query']}")
```

With:
```python
        print("\nProposed query:")
        print(f"  id:          {entry['id']}")
        print(f"  description: {entry['description']}")
        print(f"  Notify me when… {entry['query']}")
```

- [ ] **Step 6: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add assist.py tests/test_assist.py
git commit -m "feat(assist.py): statement-form system prompt and notify-me-when CLI display"
```

---

## Task 7: Update `examples.js` — lowercase first word pass

**Files:**
- Modify: `web/static/examples.js`

- [ ] **Step 1: Replace file content**

The rule: lowercase the first word of each example unless it is a proper noun (brand name, country, specific brand). Entries beginning with articles or common nouns ("The", "A", "An", "Lab", "Commercial", "Humans") get lowercased.

Replace the entire file:

```javascript
const NOTIFAI_EXAMPLES = [
  "the next James Bond actor is officially announced",
  "Apple launches a foldable iPhone",
  "Brent crude falls back to pre-war levels",
  "a European country legislates a four-day working week",
  "Ireland qualifies for a major international football tournament this year",
  "a low-cost airline launches transatlantic routes from an Irish airport for under €200",
  "the EU's social media age verification rules come into force",
  "an Alzheimer's drug is approved for use in Europe",
  "a major tech company is broken up by regulators",
  "lab-grown meat goes on sale in a European supermarket",
  "Ireland introduces a digital nomad visa",
  "the marathon world record is broken",
  "commercial supersonic passenger flights restart",
  "a major social media platform is banned in the EU",
  "humans return to the Moon",
  "Ireland sets up a sovereign wealth fund",
  "a permanent rent freeze passes into law in Ireland",
  "a new major games console is announced",
];
```

- [ ] **Step 2: Commit**

```bash
git add web/static/examples.js
git commit -m "chore(examples.js): lowercase first word for consistent notify-me-when display"
```

---

## Task 8: Update `index.html` — examples label

**Files:**
- Modify: `web/static/index.html`

- [ ] **Step 1: Change `.examples-label` text**

Find:
```html
      <div class="examples-label">For example</div>
```

Replace with:
```html
      <div class="examples-label">Notify me when…</div>
```

- [ ] **Step 2: Commit**

```bash
git add web/static/index.html
git commit -m "feat(index.html): show notify-me-when label above examples"
```

---

## Task 9: Update `dashboard.html` — query cards + step 2 preview

**Files:**
- Modify: `web/static/dashboard.html`

### 9a — Add CSS for inline prefix

- [ ] **Step 1: Add `.notify-prefix-card` CSS rule**

In the `<style>` block, after the `.query-text` rule, add:

```css
    .notify-prefix-card {
      font-size: 0.65rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--accent);
      font-weight: 600;
      margin-bottom: 0.1rem;
    }
```

### 9b — Update query card render

- [ ] **Step 2: Add "Notify me when…" prefix to query cards in `renderQueries()`**

Find the card template inside `renderQueries()`. The `.query-body` div currently is:
```javascript
          <div class="query-body">
            <div class="query-text">${escHtml(q.query_text)}</div>
```

Replace with:
```javascript
          <div class="query-body">
            <div class="notify-prefix-card">Notify me when…</div>
            <div class="query-text">${escHtml(q.query_text)}</div>
```

### 9c — Update step 2 preview

- [ ] **Step 3: Update step 2 preview to show "Notify me when…" as label**

Find the `generated-preview` div inside `step2`:
```html
        <div class="generated-preview" id="generatedPreview" style="display:block">
          <div class="field">
            <div class="field-value" id="previewQuery"></div>
          </div>
        </div>
```

Replace with:
```html
        <div class="generated-preview" id="generatedPreview" style="display:block">
          <div class="field">
            <div class="field-label">Notify me when…</div>
            <div class="field-value" id="previewQuery"></div>
          </div>
        </div>
```

(`.field-label` is already styled as muted uppercase text — reusing it here is intentional.)

- [ ] **Step 4: Commit**

```bash
git add web/static/dashboard.html
git commit -m "feat(dashboard.html): add notify-me-when prefix to query cards and step 2 preview"
```

---

## Task 10: Update `history.html` — query selector dropdown

**Files:**
- Modify: `web/static/history.html`

- [ ] **Step 1: Prefix each option in `#querySelect` with "Notify me when… "**

In the `init()` function, find:
```javascript
      queries.forEach(q => {
        const opt = document.createElement("option");
        opt.value = q.id;
        opt.textContent = q.query_text;
        sel.appendChild(opt);
      });
```

Replace with:
```javascript
      queries.forEach(q => {
        const opt = document.createElement("option");
        opt.value = q.id;
        opt.textContent = "Notify me when… " + q.query_text;
        sel.appendChild(opt);
      });
```

- [ ] **Step 2: Commit**

```bash
git add web/static/history.html
git commit -m "feat(history.html): prefix query selector options with notify-me-when"
```

---

## Final verification

- [ ] **Run full test suite**

```bash
cd notifai
python -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Manual smoke-check (self-hosted)**

```bash
# Confirm queries.yaml loads correctly
python -c "from check import load_queries; qs = load_queries('queries.yaml'); print(qs)"
```

Expected: two dicts with `query` values like `"Python 4.0 is officially released"` (no question marks).

```bash
# Confirm message framing
python -c "
from check import _make_message_params
p = _make_message_params('Python 4.0 is officially released', '2026-05-24')
print(p['messages'][0]['content'])
"
```

Expected output contains:
```
Today is 2026-05-24. Search the web and determine whether this event has occurred:
"Python 4.0 is officially released"
```

```bash
# Confirm email body format
python -c "
from check import format_email_body
print(format_email_body('Python 4.0 is officially released', 'YES', 'Released today.', ['https://python.org']))
"
```

Expected output:
```
You asked to be notified when:
Python 4.0 is officially released

It happened!
Released today.

Sources:
  - https://python.org
```
