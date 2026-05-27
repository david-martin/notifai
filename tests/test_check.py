import os
import json
import pytest
import tempfile

import yaml

from check import load_queries, parse_claude_response
from core import format_email_body, INTERVAL_DELTAS, advance_interval, make_message_params, RUNNER_SYSTEM_PROMPT


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


class TestStateFile:
    def test_load_state_returns_empty_dict_when_file_missing(self, tmp_path):
        from check import load_state
        result = load_state(str(tmp_path / "nonexistent.json"))
        assert result == {}

    def test_load_state_reads_existing_file(self, tmp_path):
        import json
        from check import load_state
        state = {"q1": "2026-05-20T07:00:00"}
        f = tmp_path / "state.json"
        f.write_text(json.dumps(state))
        result = load_state(str(f))
        assert result == state

    def test_save_state_writes_file(self, tmp_path):
        from check import save_state
        path = str(tmp_path / "state.json")
        state = {"q1": "2026-05-20T07:00:00"}
        save_state(path, state)
        import json
        written = json.loads(open(path).read())
        assert written == state

    def test_save_state_is_atomic(self, tmp_path):
        """save_state writes to a .tmp file then renames — no .tmp left behind."""
        from check import save_state
        import os
        path = str(tmp_path / "state.json")
        save_state(path, {"q1": "2026-05-20T07:00:00"})
        assert not os.path.exists(path + ".tmp")
        assert os.path.exists(path)


class TestIsDue:
    def test_never_checked_is_always_due(self):
        from check import is_due
        from datetime import datetime
        query = {"id": "q1", "interval": "1d"}
        state = {}
        assert is_due(query, state, datetime(2026, 5, 26, 7, 0, 0)) is True

    def test_checked_recently_not_due_for_daily(self):
        from check import is_due
        from datetime import datetime
        query = {"id": "q1", "interval": "1d"}
        state = {"q1": "2026-05-26T06:00:00"}
        # Only 1 hour later — not due
        assert is_due(query, state, datetime(2026, 5, 26, 7, 0, 0)) is False

    def test_checked_over_a_day_ago_is_due_for_daily(self):
        from check import is_due
        from datetime import datetime
        query = {"id": "q1", "interval": "1d"}
        state = {"q1": "2026-05-25T06:00:00"}
        # 25 hours later — due
        assert is_due(query, state, datetime(2026, 5, 26, 7, 0, 0)) is True

    def test_checked_recently_not_due_for_weekly(self):
        from check import is_due
        from datetime import datetime
        query = {"id": "q1", "interval": "1w"}
        state = {"q1": "2026-05-24T07:00:00"}
        # Only 2 days later — not due
        assert is_due(query, state, datetime(2026, 5, 26, 7, 0, 0)) is False

    def test_checked_over_a_week_ago_is_due_for_weekly(self):
        from check import is_due
        from datetime import datetime
        query = {"id": "q1", "interval": "1w"}
        state = {"q1": "2026-05-18T07:00:00"}
        # 8 days later — due
        assert is_due(query, state, datetime(2026, 5, 26, 7, 0, 0)) is True

    def test_defaults_to_daily_when_interval_missing(self):
        from check import is_due
        from datetime import datetime
        query = {"id": "q1"}  # no interval key
        state = {"q1": "2026-05-25T06:00:00"}
        # 25 hours later — due under 1d default
        assert is_due(query, state, datetime(2026, 5, 26, 7, 0, 0)) is True

    def test_monthly_not_due_after_two_weeks(self):
        from check import is_due
        from datetime import datetime
        query = {"id": "q1", "interval": "1mo"}
        state = {"q1": "2026-05-12T07:00:00"}
        # 14 days later — not due for monthly
        assert is_due(query, state, datetime(2026, 5, 26, 7, 0, 0)) is False

    def test_monthly_due_after_one_month(self):
        from check import is_due
        from datetime import datetime
        query = {"id": "q1", "interval": "1mo"}
        state = {"q1": "2026-04-25T07:00:00"}
        # 31 days later — due for monthly
        assert is_due(query, state, datetime(2026, 5, 26, 7, 0, 0)) is True


class TestRunSkipsNonDue:
    def test_run_skips_query_not_due(self, tmp_path):
        """A query checked recently within its interval is skipped."""
        import yaml
        from unittest.mock import patch
        from datetime import datetime, timedelta

        queries_data = {
            "queries": [
                {"id": "q1", "query": "something happens", "active": True, "interval": "1d"},
            ]
        }
        queries_file = tmp_path / "queries.yaml"
        queries_file.write_text(yaml.dump(queries_data))

        import json
        recent = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"q1": recent}))

        with patch("check.QUERIES_PATH", str(queries_file)), \
             patch.dict("os.environ", {
                 "NOTIFAI_STATE_PATH": str(state_file),
                 "ANTHROPIC_API_KEY": "test",
                 "RESEND_API_KEY": "test",
                 "NOTIFY_EMAIL": "test@example.com",
                 "RESEND_FROM_EMAIL": "test@example.com",
             }):
            with patch("check.anthropic.Anthropic") as MockClient:
                from check import run
                run()
                MockClient.return_value.messages.create.assert_not_called()


class TestIntervalDeltas:
    def test_has_daily_key(self):
        from datetime import timedelta
        assert INTERVAL_DELTAS["1d"] == timedelta(days=1)

    def test_has_weekly_key(self):
        from datetime import timedelta
        assert INTERVAL_DELTAS["1w"] == timedelta(weeks=1)

    def test_has_monthly_key(self):
        assert "1mo" in INTERVAL_DELTAS

    def test_all_three_keys_present(self):
        assert set(INTERVAL_DELTAS.keys()) == {"1d", "1w", "1mo"}


class TestAdvanceInterval:
    def test_daily_advances_one_day(self):
        from datetime import datetime
        dt = datetime(2026, 5, 26, 7, 0, 0)
        result = advance_interval(dt, "1d")
        assert result == datetime(2026, 5, 27, 7, 0, 0)

    def test_weekly_advances_seven_days(self):
        from datetime import datetime
        dt = datetime(2026, 5, 26, 7, 0, 0)
        result = advance_interval(dt, "1w")
        assert result == datetime(2026, 6, 2, 7, 0, 0)

    def test_monthly_advances_one_calendar_month(self):
        from datetime import datetime
        dt = datetime(2026, 5, 26, 7, 0, 0)
        result = advance_interval(dt, "1mo")
        assert result == datetime(2026, 6, 26, 7, 0, 0)

    def test_unknown_interval_falls_back_to_daily(self):
        from datetime import datetime
        dt = datetime(2026, 5, 26, 7, 0, 0)
        result = advance_interval(dt, "2d")
        assert result == datetime(2026, 5, 27, 7, 0, 0)
