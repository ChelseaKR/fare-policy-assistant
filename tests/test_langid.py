"""Tests for the character-n-gram language identifier (FIX-11).

Covers the confidence-bearing classifier that replaced the two-regex EN/ES
word-count heuristic: en/es/tl detection, an honest "unsure" verdict for short,
empty, or code-switched input, the Spanish strong-signal shortcut, and the
guard-layer delegation that keeps the old ``str`` API for existing callers while
never blocking an answer on uncertainty.
"""

from __future__ import annotations

import pytest

from assistant import guards, langid

# A small, committed mixed test set: (text, expected_lang). These are the
# clearly-single-language cases the classifier must get right (precision set).
CLEAR_CASES: list[tuple[str, str]] = [
    ("How much is the fare on the bus?", "en"),
    ("Where do I apply for the reduced fare card?", "en"),
    ("Do children under five ride free with an adult?", "en"),
    ("You may qualify for the reduced fare if you are 65 or older.", "en"),
    ("¿Cuánto cuesta el pasaje reducido?", "es"),
    ("Necesito comprobante de edad para la tarifa reducida.", "es"),
    ("¿Dónde solicito la tarjeta de descuento para personas mayores?", "es"),
    ("Los niños menores de cinco años viajan gratis con un adulto.", "es"),
    ("Magkano ang pamasahe sa bus?", "tl"),
    ("Magkano po ang senior discount sa buwanang pass?", "tl"),
    ("Saan ako mag-aaplay para sa pinababang pamasahe?", "tl"),
    ("May diskwento po ba para sa mga may kapansanan?", "tl"),
]


class TestClearDetection:
    @pytest.mark.parametrize("text,expected", CLEAR_CASES)
    def test_language_detected(self, text: str, expected: str) -> None:
        lang, conf = langid.detect(text)
        assert lang == expected
        assert 0.0 <= conf <= 1.0

    def test_precision_on_mixed_set(self) -> None:
        # Every clearly-single-language case classifies correctly and confidently.
        correct = 0
        for text, expected in CLEAR_CASES:
            result = langid.classify(text)
            if not result.unsure and result.lang == expected:
                correct += 1
        assert correct == len(CLEAR_CASES)


class TestConfidence:
    def test_confidence_in_unit_interval(self) -> None:
        for text, _ in CLEAR_CASES:
            _, conf = langid.detect(text)
            assert 0.0 <= conf <= 1.0

    def test_clear_case_is_more_confident_than_codeswitch(self) -> None:
        clear = langid.classify("¿Cuánto cuesta el pasaje reducido para personas mayores?")
        mixed = langid.classify("cuanto cuesta el day pass?")
        assert not clear.unsure
        assert clear.confidence > mixed.confidence


class TestUnsure:
    def test_empty_string_is_unsure_not_crash(self) -> None:
        lang, conf = langid.detect("")
        assert lang == langid.DEFAULT_LANGUAGE
        assert conf == pytest.approx(0.0)
        assert langid.detect_with_unsure("") == (langid.UNSURE, pytest.approx(0.0))

    def test_whitespace_only_is_unsure(self) -> None:
        assert langid.classify("   \n\t ").unsure

    def test_very_short_input_is_unsure(self) -> None:
        assert langid.classify("hi").unsure

    def test_codeswitched_returns_low_confidence_gracefully(self) -> None:
        # The spec case: must not crash, must return a valid tag and a confidence
        # well below the clear single-language cases (which sit ~0.5-0.8).
        lang, conf = langid.detect("cuanto cuesta el day pass?")
        assert lang in ("en", "es", "tl")
        assert conf < 0.35

    def test_detect_defaults_to_english_when_unsure(self) -> None:
        # detect() maps an unsure verdict to English; detect_with_unsure() is honest.
        assert langid.detect("hi")[0] == "en"
        assert langid.detect_with_unsure("hi")[0] == langid.UNSURE


class TestSpanishStrongSignal:
    def test_inverted_punctuation_boosts_spanish(self) -> None:
        assert langid.classify("¿Cuánto?").lang == "es"

    def test_accented_spanish_detected(self) -> None:
        lang, _ = langid.detect("¿Puedo obtener el descuento para años mayores?")
        assert lang == "es"


class TestGuardsDelegation:
    def test_detect_language_keeps_str_signature(self) -> None:
        assert guards.detect_language("How much is the fare?") == "en"
        assert guards.detect_language("¿Cuánto cuesta el pasaje reducido?") == "es"
        assert guards.detect_language("Magkano ang pamasahe?") == "tl"

    def test_detect_language_confident_reports_margin(self) -> None:
        lang, conf, unsure = guards.detect_language_confident("Magkano ang pamasahe?")
        assert lang == "tl"
        assert 0.0 <= conf <= 1.0
        assert unsure is False

    def test_unsure_never_blocks_and_attaches_notice(self) -> None:
        check = guards.check_input("hi")
        assert check.ok  # never blocks on uncertainty
        assert "lang:unsure" in check.flags
        assert check.notice
        # The note is rider-facing English (detection was unsure -> fallback).
        assert "English" in check.notice

    def test_confident_input_has_no_notice(self) -> None:
        check = guards.check_input("How much is the senior fare on MST?")
        assert check.ok
        assert check.notice is None
        assert "lang:unsure" not in check.flags

    def test_tagalog_unsupported_catalog_falls_back_to_english(self) -> None:
        # tl is detected but has no fixed-string catalog; a refusal renders in
        # English gracefully (NullTranslations), never an error (spec item 3).
        check = guards.check_input(
            "Magkano ang pamasahe kung ibabahagi ko ang aking numero 123-45-6789?"
        )
        assert not check.ok
        assert any(f.startswith("pii:") for f in check.flags)
        assert check.message  # English fallback text, not blank


class TestDeterminism:
    def test_repeated_calls_are_identical(self) -> None:
        first = langid.classify("¿Cuánto cuesta el pasaje?")
        second = langid.classify("¿Cuánto cuesta el pasaje?")
        assert (first.lang, first.confidence) == (second.lang, second.confidence)
