"""core.py — single source for all Claude API logic.

Every file that calls the Claude API imports from here.
Files that do not import from core.py do not touch Claude. This is the rule.

This module is purely functional and stateless. It has no knowledge of YAML,
SQLAlchemy, HTTP, or credits.
"""
import json
import logging
import os
import re

from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request as BatchRequest

logger = logging.getLogger("core")

# ---------------------------------------------------------------------------
# Constants — read from environment once at import time
# ---------------------------------------------------------------------------

MODEL = os.environ.get("NOTIFAI_MODEL", "claude-sonnet-4-6")
ASSIST_MODEL = os.environ.get("NOTIFAI_ASSIST_MODEL", "claude-haiku-4-5-20251001")

# Cache TTL for the system prompt. API accepts "1h" or "5m".
# Env var is integer seconds for readability; converted to string on use.
_cache_ttl_secs = int(os.environ.get("NOTIFAI_CACHE_TTL", "3600"))
CACHE_TTL = "1h" if _cache_ttl_secs >= 3600 else "5m"

# Canonical runner system prompt. Used by check.py, web/runner.py, and
# web/routers/queries.py (run_query_now). Do not duplicate this string elsewhere.
RUNNER_SYSTEM_PROMPT = (
    "You are an event monitor. For each event, make exactly one web search, "
    "then determine if the event has occurred. "
    "Respond ONLY with valid JSON in this exact format: "
    '{"answer": "YES" or "NO", "reason": "one sentence explanation", "sources": ["url1", "url2"]}'
)


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

def make_message_params(query_text: str, today: str) -> dict:
    """Build the complete messages.create() kwargs for a runner query.

    Uses web_search_20250305 with max_uses=1. Includes system prompt cache control.
    """
    return dict(
        model=MODEL,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": RUNNER_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral", "ttl": CACHE_TTL},
            }
        ],
        tools=[
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 1},
        ],
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
    )


def make_batch_request(custom_id: str, query_text: str, today: str) -> BatchRequest:
    """Wrap make_message_params as a BatchRequest for the Messages Batch API."""
    return BatchRequest(
        custom_id=custom_id,
        params=MessageCreateParamsNonStreaming(**make_message_params(query_text, today)),
    )


def parse_response(text: str) -> dict:
    """Strip markdown fences (if any) and JSON-parse a Claude response.

    Raises json.JSONDecodeError on malformed input — callers decide how to handle it.
    """
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        text = match.group(1)
    return json.loads(text)


def log_search_blocks(content_blocks, query_id: str) -> None:
    """Log web search result details for each web_search_tool_result block.

    Uses the standard library logger under the name 'core'.
    """
    for block in content_blocks:
        if getattr(block, "type", None) != "web_search_tool_result":
            continue
        content = getattr(block, "content", None)
        if content is None:
            logger.warning("web_search_no_content query_id=%s", query_id)
            continue
        content_type = getattr(content, "type", None)
        if content_type == "web_search_tool_result_error":
            error_code = getattr(content, "error_code", "unknown")
            logger.warning("web_search_error query_id=%s error_code=%s", query_id, error_code)
        elif isinstance(content, list):
            logger.info("web_search_results query_id=%s count=%d", query_id, len(content))
            for item in content[:5]:
                title = getattr(item, "title", "?")
                url = getattr(item, "url", "?")
                logger.info(
                    "web_search_result query_id=%s title=%.80s url=%.120s",
                    query_id, title, url,
                )
        else:
            logger.warning(
                "web_search_unexpected_content query_id=%s content_type=%s",
                query_id, type(content).__name__,
            )


def has_search_failure(content_blocks) -> str | None:
    """Return a failure description if the response contains a web search failure.

    Returns None if search succeeded (or no search block present).
    Handles both web_search_tool_result and legacy tool_result block patterns.
    """
    for block in content_blocks:
        block_type = getattr(block, "type", None)

        if block_type == "web_search_tool_result":
            content = getattr(block, "content", None)
            if content is None:
                continue
            content_type = getattr(content, "type", None)
            if content_type == "web_search_tool_result_error":
                error_code = getattr(content, "error_code", "unknown")
                return f"web_search_tool_result_error error_code={error_code}"

        elif block_type == "tool_result":
            if getattr(block, "is_error", False):
                tool_id = getattr(block, "tool_use_id", "?")
                return f"tool_result is_error=True tool_use_id={tool_id}"
            raw = getattr(block, "content", "")
            if isinstance(raw, str):
                text = raw
            elif isinstance(raw, list):
                parts = []
                for item in raw:
                    if isinstance(item, dict):
                        parts.append(item.get("text", ""))
                    else:
                        parts.append(getattr(item, "text", "") or "")
                text = " ".join(parts)
            else:
                text = str(raw) if raw else ""
            _LEGACY_FAILURE_STRINGS = (
                "rate limit", "tool execution error", "tool is currently unavailable",
                "web search is currently unavailable", "unable to retrieve current web results",
                "search could not be completed", "unable to perform web search",
            )
            if any(s in text.lower() for s in _LEGACY_FAILURE_STRINGS):
                return text

    return None
