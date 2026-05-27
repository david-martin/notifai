"""Tests for system prompt content and structure."""
from core import COMBINED_SYSTEM_PROMPT, RUNNER_SYSTEM_PROMPT


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
        assert "event" in RUNNER_SYSTEM_PROMPT
        assert "occurred" in RUNNER_SYSTEM_PROMPT

    def test_does_not_use_question_language(self):
        assert "question" not in RUNNER_SYSTEM_PROMPT
        assert "condition is met" not in RUNNER_SYSTEM_PROMPT
