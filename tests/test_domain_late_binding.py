"""Late binding of the domain profile (FIX-06).

The pipeline reads the active profile at *call* time, not import time, so
`FPA_DOMAIN` can be set any time before a request is handled and the switch
takes effect immediately. These modules are imported first (as a real app
does), then the env var is flipped, and every profile-derived value must
follow — the earlier design froze them at import and quietly ignored the
switch.
"""

from __future__ import annotations

import re

import pytest

# Import the pipeline modules up front, exactly as a running process would,
# so the test proves the values are not frozen at this import.
from assistant import config, domain, guards, retrieve
from assistant.domain import DomainProfile

# A tiny domain unlike transit: different scopes, aliases, adjacent topics, and
# fallback contact, so every accessor changes observably when it is selected.
HOUSING = DomainProfile(
    name="housing voucher policy",
    scopes=("HACLA", "SDHC"),
    aliases={"hacla": "HACLA", "sdhc": "SDHC", "san diego": "SDHC"},
    fallback_contact="your local housing authority",
    scope_topics={"eviction": re.compile(r"\b(evict|eviction)\b", re.I)},
)


@pytest.fixture
def housing_active(monkeypatch):
    domain.register("housing", HOUSING)
    monkeypatch.setenv("FPA_DOMAIN", "housing")
    assert domain.get_profile() is HOUSING
    return HOUSING


def test_config_accessors_follow_the_active_profile(housing_active):
    assert config.known_agencies() == ("HACLA", "SDHC")
    assert config.statewide_transit_info() == "your local housing authority"
    # Backward-compat module __getattr__ names track the live profile too.
    assert config.KNOWN_AGENCIES == ("HACLA", "SDHC")
    assert config.STATEWIDE_TRANSIT_INFO == "your local housing authority"


def test_detect_agencies_uses_the_switched_profile(housing_active):
    # A transit alias no longer resolves; the housing aliases now do.
    assert retrieve.detect_agencies("senior fare on SBMTD?") == []
    assert retrieve.detect_agencies("voucher rules in San Diego?") == ["SDHC"]
    # Compat constant reflects the new aliases as well.
    assert retrieve.AGENCY_ALIASES == HOUSING.aliases


def test_guard_scope_flags_follow_the_switched_profile(housing_active):
    # The transit out-of-scope topics are gone; the housing one is live.
    assert guards.OUT_OF_SCOPE_PATTERNS == {"eviction": HOUSING.scope_topics["eviction"]}
    result = guards.check_input("can they evict me for this?")
    assert not result.ok
    assert result.flags == ["scope:eviction"]
    # A former transit scope topic no longer trips the guard.
    assert guards.check_input("what disability qualifies me?").ok


def test_default_retriever_cache_is_keyed_by_profile(monkeypatch):
    # Avoid loading the real corpus: a lightweight stand-in is enough to prove
    # the cache is keyed on the profile name rather than pinned to the first
    # profile seen.
    class FakeRetriever:
        def __init__(self, chunks, cfg):
            self.profile_name = domain.get_profile().name
            self.chunks = chunks
            self.cfg = cfg

    monkeypatch.setattr(retrieve, "Retriever", FakeRetriever)
    retrieve._retriever_for.cache_clear()

    domain.register("housing", HOUSING)

    monkeypatch.setenv("FPA_DOMAIN", "transit")
    transit_retriever = retrieve.default_retriever()
    assert transit_retriever is retrieve.default_retriever()  # cached per profile
    assert transit_retriever.profile_name == domain.TRANSIT.name

    monkeypatch.setenv("FPA_DOMAIN", "housing")
    housing_retriever = retrieve.default_retriever()
    # The switch is honored: a distinct retriever bound to the new profile.
    assert housing_retriever is not transit_retriever
    assert housing_retriever.profile_name == HOUSING.name

    monkeypatch.setenv("FPA_DENSE", "1")
    dense_retriever = retrieve.default_retriever()
    assert dense_retriever is not housing_retriever
    assert dense_retriever.cfg.use_dense is True

    retrieve._retriever_for.cache_clear()
