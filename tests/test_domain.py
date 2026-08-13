"""Domain profile abstraction (R3-3): the transit defaults are unchanged, and a
new domain is a new profile, not a pipeline edit."""

from __future__ import annotations

import re

from assistant import config, domain, guards
from assistant.domain import DomainProfile
from assistant.retrieve import AGENCY_ALIASES, detect_agencies


def test_default_profile_is_transit():
    assert domain.get_profile() is domain.TRANSIT
    assert domain.get_profile().name.lower().startswith("california transit")


def test_reexports_match_the_profile():
    # The isolation did not change any value: the modules now read from the
    # profile, and the profile holds exactly what used to be hardcoded.
    p = domain.TRANSIT
    assert (
        config.KNOWN_AGENCIES
        == p.scopes
        == (
            "MST",
            "SBMTD",
            "Yolobus",
            "SacRT",
            "HTA",
            "E-tran",
        )
    )
    assert config.STATEWIDE_TRANSIT_INFO == p.fallback_contact
    assert AGENCY_ALIASES == p.aliases
    assert guards.OUT_OF_SCOPE_PATTERNS == p.scope_topics
    assert set(p.scope_topics) == {"medical_advice", "immigration", "legal_advice"}


def test_detect_agencies_uses_the_active_aliases():
    assert detect_agencies("senior fare on SBMTD?") == ["SBMTD"]
    assert detect_agencies("Monterey to Salinas") == ["MST"]


def test_a_new_domain_is_just_a_new_profile():
    # A housing-voucher assistant: different scopes, aliases, and adjacent
    # topics, reusing every line of retrieve/guards/config unchanged.
    housing = DomainProfile(
        name="housing voucher policy",
        scopes=("HACLA", "SDHC"),
        aliases={"hacla": "HACLA", "los angeles": "HACLA", "san diego": "SDHC", "sdhc": "SDHC"},
        fallback_contact="your local housing authority",
        scope_topics={"legal_advice": re.compile(r"\b(evict|lawsuit|attorney)\b", re.I)},
    )
    # Detection works with the injected alias map, no code change.
    assert detect_agencies("voucher rules in San Diego?", aliases=housing.aliases) == ["SDHC"]
    # And the profile can be registered and selected by key.
    domain.register("housing-test", housing)
    assert domain.get_profile("housing-test") is housing


def test_unknown_profile_falls_back_to_transit():
    assert domain.get_profile("no-such-domain") is domain.TRANSIT


def test_spanish_legal_advice_flagged():
    # Multilingual guard parity (FIX-05): Spanish legal-advice phrasing is
    # redirected the same way as the English "sue/lawyer" family.
    check = guards.check_input("¿Puedo demandar a la agencia si me niegan el descuento?")
    assert not check.ok
    assert "scope:legal_advice" in check.flags


def test_spanish_abogado_flagged():
    check = guards.check_input("Necesito un abogado para pelear la multa del autobús.")
    assert not check.ok
    assert "scope:legal_advice" in check.flags


def test_benign_spanish_fare_question_not_flagged_as_legal():
    # "demanda"-adjacent words must not fire: an ordinary reduced-fare question
    # stays in scope (no false positive from the new Spanish alternation).
    check = guards.check_input("¿Cuánto cuesta el pasaje reducido?")
    assert check.ok
