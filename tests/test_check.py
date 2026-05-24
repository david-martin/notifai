import os
import json
import pytest
import tempfile

import yaml

from check import load_queries, parse_claude_response, format_email_body


class TestLoadQueries:
    def test_loads_active_queries(self, tmp_path):
        data = {
            "queries": [
                {"id": "q1", "description": "Test", "query": "Is X true?", "active": True},
                {"id": "q2", "description": "Other", "query": "Is Y true?", "active": False},
            ]
        }
        f = tmp_path / "queries.yaml"
        f.write_text(yaml.dump(data))

        result = load_queries(str(f))

        assert len(result) == 1
        assert result[0]["id"] == "q1"

    def test_returns_empty_when_all_inactive(self, tmp_path):
        data = {"queries": [{"id": "q1", "description": "D", "query": "Q?", "active": False}]}
        f = tmp_path / "queries.yaml"
        f.write_text(yaml.dump(data))

        result = load_queries(str(f))

        assert result == []

    def test_loads_all_when_all_active(self, tmp_path):
        data = {
            "queries": [
                {"id": "q1", "description": "D1", "query": "Q1?", "active": True},
                {"id": "q2", "description": "D2", "query": "Q2?", "active": True},
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
            query="Have prices fallen?",
            answer="YES",
            reason="Yes, they have.",
            sources=["http://source1.com", "http://source2.com"],
        )
        assert "Have prices fallen?" in body
        assert "Yes, they have." in body
        assert "http://source1.com" in body
        assert "http://source2.com" in body

    def test_format_yes(self):
        body = format_email_body(
            query="Have prices fallen?",
            answer="YES",
            reason="Yes, they have.",
            sources=["http://source1.com"],
        )
        assert "Query:" in body
        assert "Answer:  YES" in body
        assert "Reason:" in body
        assert "Sources:" in body

    def test_format_no(self):
        body = format_email_body(
            query="Have prices fallen?",
            answer="NO",
            reason="Not yet.",
            sources=["http://source1.com"],
        )
        assert "Answer:  NO" in body

    def test_handles_empty_sources(self):
        body = format_email_body(query="Q?", answer="NO", reason="R.", sources=[])
        assert "Sources:" in body
