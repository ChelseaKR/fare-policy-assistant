"""The gettext seam: catalog loading, rider-facing message helpers, negotiation.

These guard the migration from the bespoke EN/ES dict/branch to gettext catalogs
(INTERNATIONALIZATION-STANDARD §3): a loaded catalog returns real Spanish, an
unknown tag falls back to English text, the refusal/no-support helpers render the
same text the old dict did, and ``negotiate_lang`` implements the
``<requested> → <primary subtag> → en`` fallback chain (§6). The refusal *control
flow* is asserted separately in test_guards.py; this file asserts the *text*.
"""

from __future__ import annotations

import json

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


def test_catalog_parity_gate_refuses_an_empty_template(tmp_path, monkeypatch, capsys):
    """The gate's own denominator.

    Every G5/G6 check iterates over the template's msgid set, so an empty
    template makes all of them vacuous: the gate prints "catalog parity OK: 0
    msgids" and exits 0 while nothing rider-facing is translated. G2-lite
    catches a template that drifts from the sources, but a commit that empties
    the sources and the template together drifts from nothing.
    """
    from babel.messages.catalog import Catalog
    from babel.messages.pofile import write_po

    from tools import check_catalog_parity as gate

    locales = tmp_path / "locales"
    for name in gate.CATALOGS:
        (locales / name / "LC_MESSAGES").mkdir(parents=True)
        with (locales / name / "LC_MESSAGES" / "messages.po").open("wb") as fh:
            write_po(fh, Catalog(locale=name))
    with (locales / "messages.pot").open("wb") as fh:
        write_po(fh, Catalog())

    monkeypatch.setattr(gate, "LOCALES", locales)
    monkeypatch.setattr(gate, "POT", locales / "messages.pot")
    assert gate.main() == 1
    assert "no msgids" in capsys.readouterr().err


def test_the_committed_template_is_not_empty():
    from tools import check_catalog_parity as gate

    assert len(gate._ids(gate._load(gate.POT, None))) >= 6


# ── the identity-translation check ───────────────────────────────────────────
#
# Key parity, completeness and placeholder parity are all satisfied by a Spanish
# catalog that is verbatim English: its keys match, its msgstrs are non-empty,
# and its placeholders are trivially identical. Until 2026-09-07 nothing in
# `make i18n` looked at whether any translating had happened.


def _identity_fixture(tmp_path, catalogs: dict[str, dict[str, str]], exemptions=None):
    """Write a template plus one PO per locale, and point the gate at them.

    ``catalogs`` maps locale -> {msgid: msgstr}. The template is built from the
    first locale's msgids, so key parity holds and the identity rule is the only
    thing under test.
    """
    from babel.messages.catalog import Catalog
    from babel.messages.pofile import write_po

    from tools import check_catalog_parity as gate

    locales = tmp_path / "locales"
    locales.mkdir(parents=True, exist_ok=True)

    template = Catalog()
    for msgid in next(iter(catalogs.values())):
        template.add(msgid, string="")
    with (locales / "messages.pot").open("wb") as fh:
        write_po(fh, template)

    for name, entries in catalogs.items():
        catalog = Catalog(locale=name)
        for msgid, msgstr in entries.items():
            catalog.add(msgid, string=msgstr)
        (locales / name / "LC_MESSAGES").mkdir(parents=True, exist_ok=True)
        with (locales / name / "LC_MESSAGES" / "messages.po").open("wb") as fh:
            write_po(fh, catalog)

    exemption_path = locales / "identical_by_design.json"
    if exemptions is not None:
        exemption_path.write_text(json.dumps({"identical_by_design": exemptions}), encoding="utf-8")
    return gate, locales, exemption_path


def _run_gate(monkeypatch, gate, locales, exemption_path, names):
    monkeypatch.setattr(gate, "LOCALES", locales)
    monkeypatch.setattr(gate, "POT", locales / "messages.pot")
    monkeypatch.setattr(gate, "EXEMPTIONS", exemption_path)
    monkeypatch.setattr(gate, "CATALOGS", names)
    return gate.main()


SENTENCE = "Please check the agency's website for current information."
SPANISH = "Consulte el sitio web de la agencia para obtener información actualizada."


def test_a_verbatim_english_spanish_msgstr_fails(tmp_path, monkeypatch, capsys):
    """The negative control: a real untranslated sentence must be caught.

    Every other check in this gate passes on this catalog.
    """
    gate, locales, exemptions = _identity_fixture(
        tmp_path,
        {"en": {SENTENCE: SENTENCE}, "es": {SENTENCE: SENTENCE}},
        exemptions=[],
    )
    assert _run_gate(monkeypatch, gate, locales, exemptions, ("en", "es")) == 1
    err = capsys.readouterr().err
    assert "byte-identical to the English msgid" in err


def test_a_translated_spanish_msgstr_passes(tmp_path, monkeypatch):
    gate, locales, exemptions = _identity_fixture(
        tmp_path,
        {"en": {SENTENCE: SENTENCE}, "es": {SENTENCE: SPANISH}},
        exemptions=[],
    )
    assert _run_gate(monkeypatch, gate, locales, exemptions, ("en", "es")) == 0


@pytest.mark.parametrize(
    "msgid",
    [
        "CSV",
        "OK",
        "PDF",
        "GTFS",
        "TK-12",
        "https://example.org/fares",
        "{where}",
        "2026",
        "$2.50",
    ],
)
def test_the_gate_does_not_fire_on_a_msgid_with_nothing_to_translate(tmp_path, monkeypatch, msgid):
    """The positive control: these are legitimately identical in every language.

    A blanket must-differ rule fails on all of them, and a gate with false
    positives is a gate that gets switched off.
    """
    gate, locales, exemptions = _identity_fixture(
        tmp_path,
        {"en": {msgid: msgid}, "es": {msgid: msgid}},
        exemptions=[],
    )
    assert _run_gate(monkeypatch, gate, locales, exemptions, ("en", "es")) == 0


@pytest.mark.parametrize("msgid", ["Help", "Fares", "Senior", "Clipper", "Ask a question"])
def test_an_ordinary_word_is_not_exempt_just_for_being_short(msgid):
    """The mechanical exemptions are about a msgid having no prose in it, not
    about a word happening to be spelled the same. A product name (`Clipper`)
    is a real judgement call and belongs in the reasoned list, not in a regex.
    """
    from tools import check_catalog_parity as gate

    assert gate.untranslatable_reason(msgid) is None


def test_an_exemption_with_a_reason_allows_the_identical_row(tmp_path, monkeypatch):
    gate, locales, exemptions = _identity_fixture(
        tmp_path,
        {"en": {"Clipper": "Clipper"}, "es": {"Clipper": "Clipper"}},
        exemptions=[
            {
                "locale": "es",
                "msgid": "Clipper",
                "reason": "the Bay Area regional fare card's product name, unchanged in Spanish",
            }
        ],
    )
    assert _run_gate(monkeypatch, gate, locales, exemptions, ("en", "es")) == 0


def test_an_exemption_without_a_reason_is_refused(tmp_path, monkeypatch, capsys):
    gate, locales, exemptions = _identity_fixture(
        tmp_path,
        {"en": {"Clipper": "Clipper"}, "es": {"Clipper": "Clipper"}},
        exemptions=[{"locale": "es", "msgid": "Clipper", "reason": "   "}],
    )
    assert _run_gate(monkeypatch, gate, locales, exemptions, ("en", "es")) == 1
    assert "no reason" in capsys.readouterr().err


def test_an_exemption_that_has_stopped_applying_must_be_deleted(tmp_path, monkeypatch, capsys):
    """Otherwise the list only grows, and stops describing the catalogs."""
    gate, locales, exemptions = _identity_fixture(
        tmp_path,
        {"en": {"Clipper": "Clipper"}, "es": {"Clipper": "la tarjeta Clipper"}},
        exemptions=[{"locale": "es", "msgid": "Clipper", "reason": "a product name"}],
    )
    assert _run_gate(monkeypatch, gate, locales, exemptions, ("en", "es")) == 1
    assert "which is now translated" in capsys.readouterr().err


def test_an_exemption_for_a_msgid_the_template_dropped_must_be_deleted(
    tmp_path, monkeypatch, capsys
):
    gate, locales, exemptions = _identity_fixture(
        tmp_path,
        {"en": {SENTENCE: SENTENCE}, "es": {SENTENCE: SPANISH}},
        exemptions=[{"locale": "es", "msgid": "Clipper", "reason": "a product name"}],
    )
    assert _run_gate(monkeypatch, gate, locales, exemptions, ("en", "es")) == 1
    assert "no longer declares" in capsys.readouterr().err


def test_an_english_msgstr_that_drifts_from_its_msgid_fails(tmp_path, monkeypatch, capsys):
    """`en` is the source language: its catalog is an identity map. A drifting
    English msgstr means the rendered English and the extracted source have
    quietly parted company."""
    gate, locales, exemptions = _identity_fixture(
        tmp_path,
        {"en": {SENTENCE: SENTENCE + " Thanks!"}, "es": {SENTENCE: SPANISH}},
        exemptions=[],
    )
    assert _run_gate(monkeypatch, gate, locales, exemptions, ("en", "es")) == 1
    assert "differs from its msgid" in capsys.readouterr().err


def test_the_identity_check_refuses_to_run_against_no_target_locale(tmp_path, monkeypatch, capsys):
    """A gate that cannot fail. With `en` the only catalog, the differ-from-
    source rule iterates over nothing and reports that identity holds."""
    gate, locales, exemptions = _identity_fixture(
        tmp_path, {"en": {SENTENCE: SENTENCE}}, exemptions=[]
    )
    assert _run_gate(monkeypatch, gate, locales, exemptions, ("en",)) == 1
    assert "ran against nothing" in capsys.readouterr().err


def test_a_malformed_exemption_file_fails_rather_than_reading_as_empty(
    tmp_path, monkeypatch, capsys
):
    """An exemption file that fails open turns the check it guards into one that
    cannot fail."""
    gate, locales, exemptions = _identity_fixture(
        tmp_path,
        {"en": {SENTENCE: SENTENCE}, "es": {SENTENCE: SPANISH}},
        exemptions=[],
    )
    exemptions.write_text("{not json", encoding="utf-8")
    assert _run_gate(monkeypatch, gate, locales, exemptions, ("en", "es")) == 1
    assert "could not be read as JSON" in capsys.readouterr().err


def test_the_committed_catalogs_carry_no_untranslated_string():
    """Measured, not assumed: `es` and `tl` translate all seven msgids today,
    and the reasoned exemption list is empty."""
    from tools import check_catalog_parity as gate

    catalogs = {
        name: gate._load(gate.LOCALES / name / "LC_MESSAGES" / "messages.po", name)
        for name in gate.CATALOGS
    }
    exemptions, errors = gate._load_exemptions(gate.EXEMPTIONS)
    assert errors == []
    assert exemptions == {}
    assert gate._identity_errors(catalogs, exemptions) == []
