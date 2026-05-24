# Localization Support for Web Search Queries

**Date:** 2026-05-24  
**Status:** Approved  
**Scope:** Self-hosted (`queries.yaml` + `check.py`) and SaaS (DB, API, web UI)

---

## Overview

Add optional localization fields to queries so the Claude web search tool can return geographically relevant results. In the self-hosted core, localization is authored manually in `queries.yaml`. In the web UI, users can let the browser detect their location during query creation, which is reverse-geocoded via Nominatim and stored per-query.

---

## Data Model

### Self-hosted: `queries.yaml`

Each query may include an optional `localization` block. If present, `country` is required (ISO 3166-1 alpha-2); `city`, `region`, and `timezone` (IANA) are optional.

```yaml
queries:
  - id: local-flooding
    description: Major flooding event near me
    query: Has there been a major flood warning in Seattle, Washington this week?
    active: true
    localization:
      city: Seattle
      region: Washington
      country: US
      timezone: America/Los_Angeles
```

`load_queries()` already returns the full query dict; no structural change needed — the runner just reads `q.get("localization")`.

### SaaS: `Query` table (new columns)

Four new nullable columns on the `Query` model:

| Column | Type | Notes |
|---|---|---|
| `loc_city` | `String`, nullable | e.g. `"Seattle"` |
| `loc_region` | `String`, nullable | e.g. `"Washington"` |
| `loc_country` | `String(2)`, nullable | ISO alpha-2, e.g. `"US"` |
| `loc_timezone` | `String`, nullable | IANA, e.g. `"America/Los_Angeles"` |

All four null = no localization. A new Alembic migration is required.

---

## Backend

### Prerequisite (pre-work, not part of this plan)

`make_message_params()` is extracted into a central shared module before this plan is enacted. All callers (`check.py`, `web/runner.py`, `web/routers/queries.py`) already import from it.

### Shared `make_message_params()` — one change

Add an optional `localization: dict | None = None` parameter. When present and `country` is set, inject `user_location` into the web search tool config:

```python
tool = {"type": "web_search_20250305", "name": "web_search", "max_uses": 1}
if localization and localization.get("country"):
    tool["user_location"] = {"type": "approximate", **localization}
```

All callers automatically gain localization support. No other changes to the shared function.

### `check.py` (self-hosted runner)

Pass `q.get("localization")` as the new arg when calling `make_message_params()`. One line change.

### `web/runner.py` (SaaS scheduled runner)

Build a localization dict from each query's `loc_*` columns and pass it to `make_message_params()`. One line change per call site.

### `web/routers/queries.py` (API)

**New Pydantic model:**

```python
class QueryLocalization(BaseModel):
    city: str | None = None
    region: str | None = None
    country: str = Field(..., min_length=2, max_length=2)  # required if block present
    timezone: str | None = None
```

**`QueryCreate`** gains:
```python
localization: QueryLocalization | None = None
```

**`create_query`** — stores the four `loc_*` columns from the localization block (if present).

**`_query_dict()`** — includes a nested `localization` object (or `null`) in the response, matching the POST body shape:
```json
"localization": { "city": "Seattle", "region": "Washington", "country": "US", "timezone": "America/Los_Angeles" }
```
The frontend reads `q.localization?.country` to decide whether to show the 📍 indicator.

**`run_query_now()`** — builds a localization dict from `loc_*` columns on the query row and passes it to `make_message_params()`.

---

## Frontend (Web UI)

### Query creation — Step 2 (preview/confirm)

A "Search with my location" toggle is added below the generated query preview:

```
┌─────────────────────────────────────────────┐
│ We'll monitor this daily                    │
│                                             │
│  Has there been a major storm warning...    │
│                                             │
│  📍 Search with my location  [toggle off]  │
│                                             │
│  [Add query]  [Back]  [Cancel]              │
└─────────────────────────────────────────────┘
```

**Toggle on flow:**
1. Call `navigator.geolocation.getCurrentPosition()` — triggers browser permission prompt
2. Get timezone: `Intl.DateTimeFormat().resolvedOptions().timeZone` (no permission needed)
3. Call Nominatim: `https://nominatim.openstreetmap.org/reverse?lat=…&lon=…&format=json`
4. Extract from response:
   - `city`: `address.city` → fallback to `address.town` → `address.village`
   - `region`: `address.state`
   - `country`: `address.country_code.toUpperCase()` (Nominatim returns lowercase)
5. Show detected location inline beneath the toggle: `📍 Seattle, Washington, US`
6. Store `{ city, region, country, timezone }` in `generatedQuery`

**Error states:**
- Permission denied → toggle snaps back off, inline note: "Location access denied — query will use global search"
- Nominatim call fails or times out → toggle snaps back off, inline note: "Couldn't detect location — try again"

**Toggle off** → clears localization from `generatedQuery`, hides the location label.

**POST body when localization is set:**
```json
{
  "query_text": "Has there been a major storm warning…",
  "localization": {
    "city": "Seattle",
    "region": "Washington",
    "country": "US",
    "timezone": "America/Los_Angeles"
  }
}
```

### Query cards

If a query has `loc_country` set, show a small `📍` indicator in the query meta row (next to the status badge) so users can see at a glance which queries are location-aware.

---

## Error Handling

| Failure | Behaviour |
|---|---|
| Geolocation permission denied | Toggle resets; fallback to global search (no localization stored) |
| Nominatim network error | Toggle resets; fallback to global search |
| `country` missing from Nominatim response | Toggle resets; fallback |
| `loc_country` null on a query row | `make_message_params()` omits `user_location` — no API change needed |
| Invalid country code in YAML (self-hosted) | Claude API will reject — user error, not validated at load time |

---

## Out of Scope

- Editing localization on existing queries (creation-time only)
- Per-account location defaults
- Server-side geocoding
- Validating IANA timezone strings or ISO country codes at the Python layer
- Any non-Nominatim geocoding provider
