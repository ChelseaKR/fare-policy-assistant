"""Domain profile: the one place the assistant's domain-specific knobs live.

Everything that ties this build to California transit fare policy is gathered
here behind a `DomainProfile`: the set of scopes (agencies), the names users
call them, the adjacent topics the assistant redirects rather than answers, and
the fallback contact when the corpus has no answer. The pipeline reads the active
profile (default `TRANSIT`), so adapting the harness to another policy domain
(benefits eligibility, licensing, housing) is mostly writing a new profile and a
new corpus, not editing `retrieve`, `guards`, and `config`. See
`docs/adapting.md`.

What deliberately stays out of the profile, because it is cross-domain safety,
not domain content: the PII patterns, the prompt-injection patterns, and the
eligibility-determination language detector in `guards.py`. Any policy assistant
must refuse to collect personal data, resist injection, and decline to rule on a
person; those are not knobs.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DomainProfile:
    """The domain-variable configuration. `scopes` are the entities a question
    can name (agencies here); `aliases` map what users actually type to a scope;
    `scope_topics` are adjacent subjects to redirect, not answer; and
    `fallback_contact` is where to send a rider when the corpus has no answer."""

    name: str
    scopes: tuple[str, ...]
    aliases: dict[str, str]
    fallback_contact: str
    scope_topics: dict[str, re.Pattern[str]]


# The shipped domain. Moving a value here is the whole point: another domain
# forks this object, it does not edit the pipeline.
TRANSIT = DomainProfile(
    name="California transit fare policy",
    scopes=(
        "MST",
        "SBMTD",
        "Yolobus",
        "SacRT",
        "HTA",
        "E-tran",
        "SCMTD",
        "SolTrans",
        "FAX",
        "AC Transit",
    ),
    # Aliases riders actually use, mapped to manifest agency keys.
    aliases={
        "mst": "MST",
        "monterey": "MST",
        "monterey-salinas": "MST",
        "salinas": "MST",
        "sbmtd": "SBMTD",
        "santa barbara": "SBMTD",
        "mtd": "SBMTD",
        "yolobus": "Yolobus",
        "yolo": "Yolobus",
        "sacrt": "SacRT",
        "sacramento": "SacRT",
        "hta": "HTA",
        "humboldt": "HTA",
        "eureka": "HTA",
        "arcata": "HTA",
        "redwood transit": "HTA",
        # Elk Grove Transit Services. SacRT has operated these routes since the
        # 2021 annexation, but riders still say "e-tran" and "the Elk Grove bus",
        # and the E-prefix routes keep their own fare table, so the alias points
        # at its own scope rather than folding into SacRT.
        "e-tran": "E-tran",
        "etran": "E-tran",
        "e tran": "E-tran",
        "elk grove": "E-tran",
        # Santa Cruz METRO. "metro" alone is deliberately absent: it is what
        # riders of half the agencies in California call their own operator, and
        # an alias that broad would pull unrelated questions into this scope.
        "scmtd": "SCMTD",
        "scmetro": "SCMTD",
        "santa cruz": "SCMTD",
        "santa cruz metro": "SCMTD",
        "watsonville": "SCMTD",
        "tap2cruz": "SCMTD",
        "soltrans": "SolTrans",
        "sol trans": "SolTrans",
        "solano": "SolTrans",
        "solano county transit": "SolTrans",
        "solanoexpress": "SolTrans",
        "vallejo": "SolTrans",
        "benicia": "SolTrans",
        # Deliberately NOT aliased: "clipper". Clipper is a regional fare card
        # operated by MTC and accepted by many Bay Area agencies, most of which
        # are not in this corpus. Mapping the word to SolTrans would make the
        # assistant answer agency-specific Clipper questions as if they were
        # SolTrans questions — the exact over-generalization the
        # soltrans-clipper-scope eval cases exist to prevent.
        #
        # Fresno Area Express, operated by the City of Fresno. "Handy Ride" is
        # its paratransit brand and riders name it without naming FAX.
        "fax": "FAX",
        "fresno": "FAX",
        "fresno area express": "FAX",
        "handy ride": "FAX",
        # AC Transit (Alameda-Contra Costa Transit District). "Tempo" is its
        # Line 1T bus-rapid-transit brand and "Transbay" its bridge-route
        # brand; within this corpus only AC Transit publishes fares under
        # either name, so both point here (the "handy ride"/"tap2cruz"
        # pattern). Deliberately NOT aliased: "oakland", "berkeley", and
        # "east bay" — multi-operator geographies served by agencies outside
        # this corpus (BART, WestCAT, Union City Transit), so a geography
        # alias would over-claim scope the way "metro" would have for Santa
        # Cruz.
        "ac transit": "AC Transit",
        "actransit": "AC Transit",
        "alameda-contra costa": "AC Transit",
        "tempo": "AC Transit",
        "transbay": "AC Transit",
    },
    fallback_contact="https://511.org (Bay Area) or the agency's own website",
    # Topics adjacent to fare policy that the assistant must redirect, not answer.
    scope_topics={
        "medical_advice": re.compile(
            r"(how (do|can) i (get|prove|obtain).{0,40}(disability|diagnos)|"
            r"what (disability|condition|diagnosis) (counts|qualifies)|"
            r"what (should|do|can) i (tell|say to).{0,20}(doctor|physician)|"
            r"(get|have|ask|convince) (my|a|the) (doctor|physician).{0,30}(write|sign|verif)|"
            r"qué (le )?(digo|decirle) a[l]? (mi |un )?(médico|doctor)|"
            r"(fake|pretend|claim).{0,20}disab)",
            re.I,
        ),
        "immigration": re.compile(
            r"(immigration status|undocumented|citizenship|green card|visa status|deport|"
            r"estatus migratorio|indocumentad)",
            re.I,
        ),
        # Spanish mirrors added for multilingual guard parity (FIX-05).
        # ``demandar?`` matches "demanda"/"demandar" but the trailing ``\b``
        # keeps it off unrelated words like "demandado", and no benign fare
        # term ("pasaje reducido") is in this alternation.
        "legal_advice": re.compile(
            r"\b(sue|lawsuit|legal advice|lawyer|attorney|"
            r"demandar?|demanda|abogad[oa]|asesor[íi]a legal|consejo legal)\b",
            re.I,
        ),
    },
)

_REGISTRY: dict[str, DomainProfile] = {"transit": TRANSIT}


def register(key: str, profile: DomainProfile) -> None:
    """Register a profile so `FPA_DOMAIN=<key>` selects it. A new domain calls
    this once at import (in its own module) rather than touching the pipeline."""
    _REGISTRY[key.lower()] = profile


def get_profile(name: str | None = None) -> DomainProfile:
    """The active profile. Defaults to the `FPA_DOMAIN` env value, then TRANSIT."""
    key = (name or os.environ.get("FPA_DOMAIN", "transit")).lower()
    return _REGISTRY.get(key, TRANSIT)
