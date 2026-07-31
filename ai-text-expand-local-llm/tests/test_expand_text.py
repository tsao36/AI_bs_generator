"""
Unit tests for all non-Ollama functions in expand_text.py.
Run before/after any code change as a regression gate.

Usage:
    python -m pytest tests/ -v
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_text_expand.expand_text import (
    LENGTH_INSTRUCTIONS,
    SENTENCE_COUNT_TOLERANCE,
    SYSTEM_PROMPT,
    POLISH_SYSTEM_PROMPT,
    TARGET_SENTENCE_COUNTS,
    build_correction_prompt,
    build_polish_prompt,
    build_user_prompt,
    count_sentences,
    expand_with_ollama,
    get_int_setting,
    get_setting,
    is_local_ollama_endpoint,
    load_config,
    send_ollama_chat,
)


# ── count_sentences ───────────────────────────────────────────────────────────

class TestCountSentences:
    def test_empty_string(self):
        assert count_sentences("") == 0

    def test_whitespace_only(self):
        assert count_sentences("   ") == 0

    def test_single_sentence_period(self):
        assert count_sentences("Hello world.") == 1

    def test_single_sentence_exclamation(self):
        assert count_sentences("Hello world!") == 1

    def test_single_sentence_question(self):
        assert count_sentences("How are you?") == 1

    def test_two_english_sentences(self):
        assert count_sentences("Hello world. How are you?") == 2

    def test_five_english_sentences(self):
        text = "First. Second. Third. Fourth. Fifth."
        assert count_sentences(text) == 5

    def test_chinese_full_stop(self):
        assert count_sentences("你好。") == 1

    def test_two_chinese_sentences(self):
        assert count_sentences("你好。再見。") == 2

    def test_chinese_exclamation(self):
        assert count_sentences("很好！再見。") == 2

    def test_no_punctuation_counts_as_one(self):
        assert count_sentences("No punctuation here at all") == 1

    def test_sentence_with_closing_quote(self):
        assert count_sentences('He said "Hello."') == 1

    def test_extra_whitespace_normalised(self):
        assert count_sentences("First.   Second.") == 2


# ── build_user_prompt ─────────────────────────────────────────────────────────

class TestBuildUserPrompt:
    def test_contains_length_instruction(self):
        prompt = build_user_prompt("Hello.", "two_sentences", "english")
        assert LENGTH_INSTRUCTIONS["two_sentences"] in prompt

    def test_contains_input_text(self):
        prompt = build_user_prompt("Some input text.", "two_sentences", "english")
        assert "Some input text." in prompt

    def test_contains_language_instruction(self):
        prompt = build_user_prompt("你好。", "two_sentences", "chinese-traditional")
        assert "Traditional Chinese" in prompt

    def test_expand_directive_present(self):
        prompt = build_user_prompt("Hello.", "five_sentences", "english")
        assert "Expand" in prompt

    def test_all_length_modes_produce_prompt(self):
        for mode in LENGTH_INSTRUCTIONS:
            prompt = build_user_prompt("Test.", mode, "english")
            assert len(prompt) > 50


# ── build_correction_prompt ───────────────────────────────────────────────────

class TestBuildCorrectionPrompt:
    def test_mentions_target_count(self):
        prompt = build_correction_prompt("Original.", "Draft sentence.", 5, "english")
        assert "5" in prompt

    def test_mentions_actual_count(self):
        # "Draft sentence." = 1 sentence; prompt should say 1
        prompt = build_correction_prompt("Original.", "Draft sentence.", 5, "english")
        assert "1" in prompt

    def test_contains_original_text(self):
        prompt = build_correction_prompt("Original input.", "Previous output.", 3, "english")
        assert "Original input." in prompt

    def test_contains_previous_output(self):
        prompt = build_correction_prompt("Original input.", "Previous output.", 3, "english")
        assert "Previous output." in prompt

    def test_contains_language_instruction(self):
        prompt = build_correction_prompt("你好。", "Draft.", 2, "chinese-traditional")
        assert "Traditional Chinese" in prompt

    def test_no_markdown_instruction(self):
        prompt = build_correction_prompt("Original.", "Draft.", 3, "english")
        assert "markdown" in prompt.lower()


# ── build_polish_prompt ───────────────────────────────────────────────────────

class TestBuildPolishPrompt:
    def test_contains_draft(self):
        prompt = build_polish_prompt("My draft text.", "english")
        assert "My draft text." in prompt

    def test_language_instruction_comes_before_draft(self):
        prompt = build_polish_prompt("My draft.", "english")
        lang_pos = prompt.index("English")
        draft_pos = prompt.index("My draft.")
        assert lang_pos < draft_pos, "Language instruction must appear before the draft"

    def test_traditional_chinese_language_first(self):
        prompt = build_polish_prompt("草稿。", "chinese-traditional")
        lang_pos = prompt.index("Traditional Chinese")
        draft_pos = prompt.index("草稿。")
        assert lang_pos < draft_pos

    def test_no_translate_instruction_present(self):
        prompt = build_polish_prompt("Draft.", "english")
        assert "Do NOT translate" in prompt

    def test_no_add_remove_instruction_present(self):
        prompt = build_polish_prompt("Draft.", "english")
        assert "Do NOT add" in prompt or "do not add" in prompt.lower()


# ── is_local_ollama_endpoint ──────────────────────────────────────────────────

class TestIsLocalOllamaEndpoint:
    def test_localhost(self):
        assert is_local_ollama_endpoint("http://localhost:11434") is True

    def test_127_0_0_1(self):
        assert is_local_ollama_endpoint("http://127.0.0.1:11434") is True

    def test_ipv6_loopback(self):
        # Python's urlparse needs brackets around IPv6 addresses
        assert is_local_ollama_endpoint("http://[::1]:11434") is True

    def test_remote_ip(self):
        assert is_local_ollama_endpoint("http://192.168.1.100:11434") is False

    def test_remote_hostname(self):
        assert is_local_ollama_endpoint("http://ollama.example.com:11434") is False

    def test_uppercase_localhost(self):
        assert is_local_ollama_endpoint("http://LOCALHOST:11434") is True


# ── load_config ───────────────────────────────────────────────────────────────

class TestLoadConfig:
    def test_returns_empty_dict_for_none(self):
        assert load_config(None) == {}

    def test_returns_empty_dict_for_nonexistent_path(self):
        assert load_config(Path("/nonexistent/path/config.json")) == {}

    def test_loads_valid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"LOCAL_LLM_MODEL": "llama3"}, f)
            tmp = Path(f.name)
        try:
            cfg = load_config(tmp)
            assert cfg["LOCAL_LLM_MODEL"] == "llama3"
        finally:
            tmp.unlink()

    def test_loads_json_with_utf8_bom(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            # Write UTF-8 BOM + valid JSON
            f.write(b"\xef\xbb\xbf" + b'{"key": "value"}')
            tmp = Path(f.name)
        try:
            cfg = load_config(tmp)
            assert cfg["key"] == "value"
        finally:
            tmp.unlink()


# ── get_setting ───────────────────────────────────────────────────────────────

class TestGetSetting:
    def test_returns_default_when_not_in_config(self):
        assert get_setting({}, "MISSING_KEY", "default_val") == "default_val"

    def test_returns_config_value(self):
        assert get_setting({"LOCAL_LLM_MODEL": "qwen"}, "LOCAL_LLM_MODEL", "default") == "qwen"

    def test_env_var_overrides_config(self, monkeypatch):
        monkeypatch.setenv("LOCAL_LLM_MODEL", "env_model")
        assert get_setting({"LOCAL_LLM_MODEL": "config_model"}, "LOCAL_LLM_MODEL", "default") == "env_model"

    def test_empty_string_in_config_falls_back_to_default(self):
        assert get_setting({"LOCAL_LLM_MODEL": ""}, "LOCAL_LLM_MODEL", "default") == "default"

    def test_whitespace_trimmed_from_env(self, monkeypatch):
        monkeypatch.setenv("LOCAL_LLM_MODEL", "  model_name  ")
        result = get_setting({}, "LOCAL_LLM_MODEL", "default")
        assert result == "model_name"


# ── get_int_setting ───────────────────────────────────────────────────────────

class TestGetIntSetting:
    def test_returns_default_when_missing(self):
        assert get_int_setting({}, "MISSING", 42) == 42

    def test_returns_int_from_config(self):
        assert get_int_setting({"OLLAMA_NUM_GPU": 8}, "OLLAMA_NUM_GPU", 999) == 8

    def test_env_var_overrides(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_NUM_GPU", "4")
        assert get_int_setting({"OLLAMA_NUM_GPU": 8}, "OLLAMA_NUM_GPU", 999) == 4

    def test_invalid_env_var_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_NUM_GPU", "not_a_number")
        assert get_int_setting({}, "OLLAMA_NUM_GPU", 999) == 999

    def test_invalid_config_value_falls_back_to_default(self):
        assert get_int_setting({"OLLAMA_NUM_GPU": "bad"}, "OLLAMA_NUM_GPU", 999) == 999


# ── send_ollama_chat (mocked) ─────────────────────────────────────────────────

class TestSendOllamaChat:
    def _make_response(self, content: str) -> bytes:
        return json.dumps({"message": {"content": content}}).encode("utf-8")

    def test_returns_content_from_response(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = self._make_response("Hello response")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("ai_text_expand.expand_text.build_opener") as mock_build_opener:
            opener = MagicMock()
            opener.open.return_value = mock_resp
            mock_build_opener.return_value = opener
            result = send_ollama_chat(
                [{"role": "user", "content": "test"}],
                "llama3", "http://127.0.0.1:11434", 30, 1
            )
        assert result == "Hello response"

    def test_raises_on_empty_content(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"message": {"content": ""}}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("ai_text_expand.expand_text.build_opener") as mock_build_opener:
            opener = MagicMock()
            opener.open.return_value = mock_resp
            mock_build_opener.return_value = opener
            with pytest.raises(RuntimeError, match="empty content"):
                send_ollama_chat([{"role": "user", "content": "test"}],
                                 "llama3", "http://127.0.0.1:11434", 30, 1)

    def test_raises_on_missing_message_key(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"no_message": "here"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("ai_text_expand.expand_text.build_opener") as mock_build_opener:
            opener = MagicMock()
            opener.open.return_value = mock_resp
            mock_build_opener.return_value = opener
            with pytest.raises(RuntimeError, match="Unexpected Ollama response"):
                send_ollama_chat([{"role": "user", "content": "test"}],
                                 "llama3", "http://127.0.0.1:11434", 30, 1)

    def test_strips_whitespace_from_content(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = self._make_response("  padded  ")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("ai_text_expand.expand_text.build_opener") as mock_build_opener:
            opener = MagicMock()
            opener.open.return_value = mock_resp
            mock_build_opener.return_value = opener
            result = send_ollama_chat([{"role": "user", "content": "test"}],
                                      "llama3", "http://127.0.0.1:11434", 30, 1)
        assert result == "padded"


# ── expand_with_ollama (mocked) ───────────────────────────────────────────────

class TestExpandWithOllama:
    """Tests for expand_with_ollama that mock send_ollama_chat to avoid needing Ollama."""

    def _expand(self, text: str, length_mode: str, mock_responses: list[str],
                output_language: str = "auto") -> str:
        responses = iter(mock_responses)
        with patch("ai_text_expand.expand_text.send_ollama_chat", side_effect=responses):
            return expand_with_ollama(
                text, "llama3", "http://127.0.0.1:11434", 30, length_mode, 1, output_language
            )

    def test_returns_result_when_sentence_count_matches(self):
        result = self._expand(
            "Hello.",
            "two_sentences",
            ["First sentence. Second sentence.",   # expand pass — 2 sentences ✓
             "First sentence. Second sentence."],  # polish pass
        )
        assert "First sentence" in result

    def test_retries_when_count_wrong_then_succeeds(self):
        result = self._expand(
            "Hello.",
            "two_sentences",
            ["Only one sentence.",                        # expand — wrong count
             "First sentence. Second sentence.",          # correction — correct
             "First sentence. Second sentence."],         # polish
        )
        assert "First sentence" in result

    def test_traditional_chinese_language_hint_applied(self):
        captured = []

        def capture(messages, *args, **kwargs):
            captured.append(messages)
            return "第一句話。第二句話。"

        with patch("ai_text_expand.expand_text.send_ollama_chat", side_effect=capture):
            expand_with_ollama(
                "祝你們永浴愛河。",
                "llama3", "http://127.0.0.1:11434", 30, "two_sentences", 1, "auto"
            )

        # First call is the expand pass — its user prompt must contain Traditional Chinese hint
        first_user_msg = captured[0][1]["content"]
        assert "Traditional Chinese" in first_user_msg

    def test_english_input_uses_english_instruction(self):
        captured = []

        def capture(messages, *args, **kwargs):
            captured.append(messages)
            return "First sentence. Second sentence."

        with patch("ai_text_expand.expand_text.send_ollama_chat", side_effect=capture):
            expand_with_ollama(
                "Please follow up with the customer.",
                "llama3", "http://127.0.0.1:11434", 30, "two_sentences", 1, "auto"
            )

        first_user_msg = captured[0][1]["content"]
        assert "English" in first_user_msg

    def test_ambiguous_chinese_defaults_to_traditional(self):
        # "真的很棒" has no distinctive chars — must default to Traditional, not Simplified
        captured = []

        def capture(messages, *args, **kwargs):
            captured.append(messages)
            return "第一句話。第二句話。"

        with patch("ai_text_expand.expand_text.send_ollama_chat", side_effect=capture):
            expand_with_ollama(
                "真的很棒",
                "llama3", "http://127.0.0.1:11434", 30, "two_sentences", 1, "auto"
            )

        first_user_msg = captured[0][1]["content"]
        assert "Traditional Chinese" in first_user_msg, (
            "Ambiguous Chinese input must default to Traditional, not Simplified"
        )

    def test_polish_pass_uses_polish_system_prompt(self):
        # The polish pass only runs after all 3 retries are exhausted.
        # Use five_sentences (tolerance=1): 3 sentences is NOT acceptable (|3-5|=2 > 1)
        # but is within the hard-fail threshold (max(1,1*2)=2), so polish runs.
        three_sent = "Sentence one. Sentence two. Sentence three."
        captured = []

        def capture(messages, *args, **kwargs):
            captured.append(messages)
            return three_sent  # always 3 sentences

        with patch("ai_text_expand.expand_text.send_ollama_chat", side_effect=capture):
            expand_with_ollama(
                "Hello.",
                "llama3", "http://127.0.0.1:11434", 30, "five_sentences", 1, "auto"
            )

        # Last call is the polish pass
        last_system_msg = captured[-1][0]["content"]
        assert "NEVER translate" in last_system_msg, (
            f"Polish pass should use POLISH_SYSTEM_PROMPT. Got: {last_system_msg[:100]}"
        )

    def test_hard_fail_when_count_way_off(self):
        with pytest.raises(RuntimeError, match="sentence"):
            self._expand(
                "Hello.",
                "one_sentence",   # tolerance = 0, hard_fail_threshold = 1
                # All responses return 10 sentences — far beyond tolerance
                ["One. Two. Three. Four. Five. Six. Seven. Eight. Nine. Ten."] * 5,
            )

    def test_accepts_result_within_tolerance(self):
        # paragraph tolerance = 2, so 12 sentences should be accepted
        twelve = " ".join(f"Sentence {i}." for i in range(1, 13))
        result = self._expand("Hello.", "paragraph", [twelve, twelve])
        assert result  # should not raise

    def test_polish_failure_falls_back_to_draft(self):
        from urllib.error import URLError

        call_count = [0]

        def side_effect(messages, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return "First sentence. Second sentence."  # expand pass ok
            raise URLError("connection refused")           # polish pass fails

        with patch("ai_text_expand.expand_text.send_ollama_chat", side_effect=side_effect):
            result = expand_with_ollama(
                "Hello.", "llama3", "http://127.0.0.1:11434", 30, "two_sentences", 1, "auto"
            )
        assert "First sentence" in result  # should return the draft, not raise


# ── constants sanity checks ───────────────────────────────────────────────────

class TestConstants:
    def test_all_length_modes_have_target_counts(self):
        for mode in LENGTH_INSTRUCTIONS:
            assert mode in TARGET_SENTENCE_COUNTS, f"Missing target count for {mode}"

    def test_all_length_modes_have_tolerance(self):
        for mode in LENGTH_INSTRUCTIONS:
            assert mode in SENTENCE_COUNT_TOLERANCE, f"Missing tolerance for {mode}"

    def test_system_prompt_not_empty(self):
        assert len(SYSTEM_PROMPT) > 100

    def test_polish_system_prompt_says_never_translate(self):
        assert "NEVER translate" in POLISH_SYSTEM_PROMPT

    def test_tolerances_non_negative(self):
        for mode, tol in SENTENCE_COUNT_TOLERANCE.items():
            assert tol >= 0, f"Negative tolerance for {mode}"

    def test_short_modes_have_zero_tolerance(self):
        assert SENTENCE_COUNT_TOLERANCE["one_sentence"] == 0
        assert SENTENCE_COUNT_TOLERANCE["two_sentences"] == 0
