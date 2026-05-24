import json
import pytest
import yaml

from assist import parse_proposed_query, check_duplicate_id, append_query


class TestParseProposedQuery:
    def test_parses_valid_json(self):
        text = json.dumps({"id": "oil-prices", "description": "Oil prices", "query": "Have prices fallen?"})
        result = parse_proposed_query(text)
        assert result["id"] == "oil-prices"
        assert result["description"] == "Oil prices"
        assert result["query"] == "Have prices fallen?"

    def test_raises_on_malformed_json(self):
        with pytest.raises(json.JSONDecodeError):
            parse_proposed_query("not json")

    def test_handles_json_in_markdown_block(self):
        inner = json.dumps({"id": "test", "description": "D", "query": "Q?"})
        text = f"```json\n{inner}\n```"
        result = parse_proposed_query(text)
        assert result["id"] == "test"


class TestCheckDuplicateId:
    def test_returns_true_when_id_exists(self, tmp_path):
        data = {"queries": [{"id": "existing", "description": "D", "query": "Q?", "active": True}]}
        f = tmp_path / "queries.yaml"
        f.write_text(yaml.dump(data))
        assert check_duplicate_id("existing", str(f)) is True

    def test_returns_false_when_id_absent(self, tmp_path):
        data = {"queries": [{"id": "existing", "description": "D", "query": "Q?", "active": True}]}
        f = tmp_path / "queries.yaml"
        f.write_text(yaml.dump(data))
        assert check_duplicate_id("new-id", str(f)) is False


class TestAppendQuery:
    def test_appends_new_entry_with_active_true(self, tmp_path):
        data = {"queries": [{"id": "q1", "description": "D1", "query": "Q1?", "active": True}]}
        f = tmp_path / "queries.yaml"
        f.write_text(yaml.dump(data))

        append_query({"id": "q2", "description": "D2", "query": "Q2?"}, str(f))

        loaded = yaml.safe_load(f.read_text())
        assert len(loaded["queries"]) == 2
        new = loaded["queries"][1]
        assert new["id"] == "q2"
        assert new["active"] is True

    def test_does_not_modify_existing_entries(self, tmp_path):
        data = {"queries": [{"id": "q1", "description": "D1", "query": "Q1?", "active": True}]}
        f = tmp_path / "queries.yaml"
        f.write_text(yaml.dump(data))

        append_query({"id": "q2", "description": "D2", "query": "Q2?"}, str(f))

        loaded = yaml.safe_load(f.read_text())
        assert loaded["queries"][0]["id"] == "q1"
