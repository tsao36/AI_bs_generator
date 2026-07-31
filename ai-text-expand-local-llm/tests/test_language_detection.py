"""
Unit tests for language detection and instruction building.
Run BEFORE making code changes to establish a baseline, and AFTER to catch regressions.

Usage:
    python -m pytest tests/test_language_detection.py -v
or via the test script:
    powershell -File scripts/test_expand.ps1
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_text_expand.expand_text import (
    build_language_instruction,
    detect_chinese_script,
    detect_language_hint,
    normalize_language_hint,
)


# ── detect_language_hint ─────────────────────────────────────────────────────

def test_detect_traditional_chinese():
    assert detect_language_hint("祝你們永浴愛河，白頭偕老") == "chinese"

def test_detect_simplified_chinese():
    assert detect_language_hint("祝你们永浴爱河，白头偕老") == "chinese"

def test_detect_english():
    assert detect_language_hint("Please follow up with the customer.") == "english"

def test_detect_japanese():
    assert detect_language_hint("日本語のテキストです。") == "japanese"

def test_detect_korean():
    assert detect_language_hint("안녕하세요 한국어 텍스트입니다.") == "korean"

def test_detect_empty_returns_unknown():
    assert detect_language_hint("") == "unknown"
    assert detect_language_hint("   ") == "unknown"

def test_detect_mixed_returns_mixed():
    # Exactly 8 English letters and 8 Chinese chars → ratio 0.5 < 0.6 threshold → mixed
    result = detect_language_hint("AI 技術 AI 系統 AI 研究 AI 發展")
    assert result == "mixed"


# ── detect_chinese_script ────────────────────────────────────────────────────

def test_script_traditional():
    # 們 is in TRADITIONAL_ONLY_CHARS
    assert detect_chinese_script("你們好") == "traditional"

def test_script_simplified():
    # 们 is in SIMPLIFIED_ONLY_CHARS
    assert detect_chinese_script("你们好") == "simplified"

def test_script_unknown_for_no_markers():
    # No Traditional/Simplified marker chars
    assert detect_chinese_script("你好") == "unknown"


# ── normalize_language_hint ──────────────────────────────────────────────────

def test_normalize_zh_tw():
    assert normalize_language_hint("zh-tw") == "chinese-traditional"

def test_normalize_zh_hant():
    assert normalize_language_hint("zh-hant") == "chinese-traditional"

def test_normalize_zh_cn():
    assert normalize_language_hint("zh-cn") == "chinese-simplified"

def test_normalize_zh_hans():
    assert normalize_language_hint("zh-hans") == "chinese-simplified"

def test_normalize_auto():
    assert normalize_language_hint("auto") == "auto"

def test_normalize_unknown_falls_back_to_auto():
    assert normalize_language_hint("klingon") == "auto"

def test_normalize_case_insensitive():
    assert normalize_language_hint("English") == "english"
    assert normalize_language_hint("ZH-TW") == "chinese-traditional"


# ── build_language_instruction ───────────────────────────────────────────────

def test_instruction_traditional_chinese_says_traditional():
    instr = build_language_instruction("chinese-traditional")
    assert "Traditional Chinese" in instr
    assert "Simplified" in instr  # must warn about what NOT to use

def test_instruction_traditional_never_says_english():
    instr = build_language_instruction("chinese-traditional")
    # Instruction must not tell the model to output English
    assert "English" not in instr or "not" in instr.lower()

def test_instruction_simplified_chinese():
    instr = build_language_instruction("chinese-simplified")
    assert "Simplified Chinese" in instr

def test_instruction_english():
    instr = build_language_instruction("english")
    assert "English" in instr

def test_instruction_japanese():
    instr = build_language_instruction("japanese")
    assert "Japanese" in instr

def test_instruction_korean():
    instr = build_language_instruction("korean")
    assert "Korean" in instr


# ── end-to-end language chain ────────────────────────────────────────────────

def _full_language_chain(text: str) -> str:
    """Simulate what expand_with_ollama does to pick a language hint."""
    raw = detect_language_hint(text)
    if raw == "chinese":
        script = detect_chinese_script(text)
        if script == "traditional":
            return "chinese-traditional"
        if script == "simplified":
            return "chinese-simplified"
    return raw

def test_traditional_text_uses_traditional_instruction():
    hint = _full_language_chain("祝你們永浴愛河，白頭偕老")
    assert hint == "chinese-traditional"
    instr = build_language_instruction(hint)
    assert "Traditional Chinese" in instr

def test_simplified_text_uses_simplified_instruction():
    hint = _full_language_chain("祝你们永浴爱河，白头偕老")
    assert hint == "chinese-simplified"
    instr = build_language_instruction(hint)
    assert "Simplified Chinese" in instr

def test_english_text_uses_english_instruction():
    hint = _full_language_chain("Please follow up with the customer.")
    assert hint == "english"
    instr = build_language_instruction(hint)
    assert "English" in instr
