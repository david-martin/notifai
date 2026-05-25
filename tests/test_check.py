import os
import json
import pytest
import tempfile

import yaml

from check import load_queries, parse_claude_response, format_email_body
from core import make_message_params, RUNNER_SYSTEM_PROMPT


class TestLoadQueries:
    def test_loads_active_queries(self, tmp_path):
        data = {
            "queries": [
                {"id": "q1", "description": "Test", "query": "something happens", "active": True},
                {"id": "q2", "description": "Other", "query": "another thing happens", "active": False},
            ]
        }
        f = tmp_path / "queries.yaml"
        f.write_text(yaml.dump(data))

        result = load_queries(str(f))

        assert len(result) == 1
        assert result[0]["id"] == "q1"

    def test_returns_empty_when_all_inactive(self, tmp_path):
        data = {"queries": [{"id": "q1", "description": "D", "query": "something happens", "active": False}]}
        f = tmp_path / "queries.yaml"
        f.write_text(yaml.dump(data))

        result = load_queries(str(f))

        assert result == []

    def test_loads_all_when_all_active(self, tmp_path):
        data = {
            "queries": [
                {"id": "q1", "description": "D1", "query": "something happens", "active": True},
                {"id": "q2", "description": "D2", "query": "another thing happens", "active": True},
            ]
        }
        f = tmp_path / "queries.yaml"
        f.write_text(yaml.dump(data))

        result = load_queries(str(f))

        assert len(result) == 2


class TestParseClaudeResponse:
    def test_parses_yes_response(self):
        text = json.dumps({"answer": "YES", "reason": "Prices fell.", "sources": ["http://x.com"]})
        result = parse_claude_response(text)
        assert result["answer"] == "YES"
        assert result["reason"] == "Prices fell."
        assert result["sources"] == ["http://x.com"]

    def test_parses_no_response(self):
        text = json.dumps({"answer": "NO", "reason": "Not yet.", "sources": []})
        result = parse_claude_response(text)
        assert result["answer"] == "NO"

    def test_raises_on_malformed_json(self):
        with pytest.raises(json.JSONDecodeError):
            parse_claude_response("not json at all")

    def test_raises_on_partial_json(self):
        with pytest.raises(json.JSONDecodeError):
            parse_claude_response("{answer: YES}")

    def test_handles_json_in_markdown_block(self):
        text = "```json\n" + json.dumps({"answer": "YES", "reason": "R", "sources": []}) + "\n```"
        result = parse_claude_response(text)
        assert result["answer"] == "YES"


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
        params = make_message_params("the artemis mission lands on the moon", "2026-05-24")
        content = params["messages"][0]["content"]
        assert "determine whether this event has occurred" in content
        assert '"the artemis mission lands on the moon"' in content
        assert "2026-05-24" in content

    def test_does_not_use_question_framing(self):
        params = make_message_params("the artemis mission lands on the moon", "2026-05-24")
        content = params["messages"][0]["content"]
        assert "Search the web and answer:" not in content


class TestSystemPrompt:
    def test_uses_event_language(self):
        assert "event" in RUNNER_SYSTEM_PROMPT
        assert "occurred" in RUNNER_SYSTEM_PROMPT

    def test_does_not_use_question_language(self):
        assert "question" not in RUNNER_SYSTEM_PROMPT
