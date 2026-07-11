"""The gettext seam: catalog loading, rider-facing message helpers, negotiation.

These guard the migration from the bespoke EN/ES dict/branch to gettext catalogs
(INTERNATIONALIZATION-STANDARD §3): a loaded catalog returns real Spanish, an
unknown tag falls back to English text, the refusal/no-support helpers render the
same text the old dict did, and ``negotiate_lang`` implements the
``<requested> → <primary subtag> → en`` fallback chain (§6). The refusal *control
flow* is asserted separately in test_guards.py; this file asserts the *text*.
"""

from __future__ import annotations

import pytest

from assistant.i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    get_translation,
    negotiate_lang,
    no_support_message,
    refusal_message,
)


def test_get_translation_loads_spanish_catalog() -> None:
    assert (
        refusal_message(get_translation("es"), "injection")
        == "Solo puedo responder preguntas sobre las políticas de tarifas "
        "publicadas. Si necesita otra ayuda, comuníquese con el servicio al "
        "cliente de la agencia de tránsito."
    )


def test_get_translation_loads_tagalog_catalog() -> None:
    assert (
        refusal_message(get_translation("tl"), "injection")
        == "Maaari lamang akong sumagot sa mga tanong tungkol sa mga inilathalang "
        "patakaran sa pamasahe. Kung kailangan mo ng ibang tulong, makipag-ugnayan "
        "sa customer service ng transit agency."
    )


def test_get_translation_english_is_source_text() -> None:
    msg = refusal_message(get_translation("en"), "injection")
    assert msg.startswith("I can only answer questions about published transit fare policies.")


def test_get_translation_unknown_tag_falls_back_to_source() -> None:
    # fallback=True → NullTranslations returns the English msgid unchanged.
    en = refusal_message(get_translation("en"), "pii")
    xx = refusal_message(get_translation("xx"), "pii")
    assert xx == en


@pytest.mark.parametrize(
    ("lang", "kind", "needle"),
    [
        ("es", "pii", "datos personales"),
        ("es", "scope", "asuntos médicos"),
        ("es", "injection", "tarifas publicadas"),
        ("tl", "pii", "personal na detalye"),
        ("tl", "scope", "usaping medikal"),
        ("tl", "injection", "inilathalang patakaran"),
        ("en", "pii", "personal details"),
        ("en", "scope", "medical, immigration, or legal"),
        ("en", "injection", "published transit fare policies"),
    ],
)
def test_refusal_message(lang: str, kind: str, needle: str) -> None:
    assert needle in refusal_message(get_translation(lang), kind)


def test_no_support_message_english_agency_and_statewide() -> None:
    en = get_translation("en")
    with_agency = no_support_message(en, agency_hint="MST", statewide_info="STATEWIDE")
    without = no_support_message(en, agency_hint=None, statewide_info="STATEWIDE")
    assert with_agency == (
        "I don't have a published policy document that answers that, and I "
        "won't guess about fares or eligibility. Please check the agency's "
        "website or customer service for current information."
    )
    # No-agency branch renders the statewide pointer via the {statewide} field.
    assert "your transit agency directly, or STATEWIDE for current" in without


def test_no_support_message_spanish_preserves_no_determination_stance() -> None:
    es = get_translation("es")
    msg = no_support_message(es, agency_hint=None, statewide_info="INFO")
    # The refusal-to-guess stance must survive translation (safety, not just text).
    assert "no voy a adivinar sobre tarifas o elegibilidad" in msg
    assert "su agencia de tránsito directamente, o INFO" in msg


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, "en"),
        ("", "en"),
        ("   ", "en"),
        ("es", "es"),
        ("ES", "es"),
        ("es-MX", "es"),  # primary-subtag fallback
        ("tl", "tl"),
        ("tl-PH", "tl"),
        ("fr", "en"),  # unsupported → default
        ("*", "en"),  # wildcard → default
        ("en-US,es;q=0.9", "en"),  # highest-q primary matches en
        ("fr;q=0.2, es;q=0.8", "es"),  # q-weighted selection
        ("de-DE, es", "es"),  # first unsupported, tie broken by order to es
        ("es;q=0", "en"),  # q=0 means "not acceptable"
        ("es;q=notanumber", "en"),  # malformed q → dropped
        (";q=0.5, es", "es"),  # empty tag skipped
    ],
)
def test_negotiate_lang(header: str | None, expected: str) -> None:
    assert negotiate_lang(header) == expected


def test_default_language_is_supported() -> None:
    assert DEFAULT_LANGUAGE in SUPPORTED_LANGUAGES
