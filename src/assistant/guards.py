"""Input and output guards.

Input: PII detection (refuse and explain; nothing is logged), scope checks for
topics the assistant must not advise on.

Output: the determination-language check — the assistant explains published
criteria ("you may qualify if…") and never decides anyone's eligibility
("you qualify"). Both English and Spanish patterns are enforced. Eval suites
assert these same rules, so a regression here fails the build twice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from assistant import domain, langid
from assistant.i18n import get_translation, language_uncertain_notice, refusal_message

# ── input guards ─────────────────────────────────────────────────────────────

PII_PATTERNS: dict[str, re.Pattern[str]] = {
    # Accept the common compact, space-separated, and hyphenated forms. These
    # identifiers are never needed for fare guidance, so privacy wins over
    # trying to infer whether a nine-digit token was really intended as an SSN.
    "ssn": re.compile(r"(?<!\d)\d{3}(?P<ssn_sep>[- ]?)\d{2}(?P=ssn_sep)\d{4}(?!\d)"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    "phone": re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"),
    # Multilingual parity (FIX-05): the lead-ins are English, Spanish, and
    # Tagalog, but
    # the digit tail is unchanged, so "nací el 3 de mayo de 1961" trips (the "3"
    # falls within 20 chars of the lead-in) while a bare Spanish phrase with no
    # date does not. Detection must not weaken as we add languages.
    "dob": re.compile(
        r"(?:\b(?:born on|date of birth|birthday is|dob)\b"
        r"|nac[íi] el|fecha de nacimiento|mi cumplea[ñn]os es|naci[óo] el"
        r"|ipinanganak ako noong|petsa ng kapanganakan|kaarawan ko ay)"
        r".{0,20}\d",
        re.I,
    ),
    "medicare_id": re.compile(
        r"\b\d[A-Z][A-Z0-9]\d[\s-]?[A-Z][A-Z0-9]\d[\s-]?[A-Z]{2}\d{2}\b",
        re.I,
    ),
}


# Topics adjacent to the domain that the assistant must redirect, not answer,
# are domain-specific, so sourced from the active profile at call time (see
# check_input below and src/assistant/domain.py) rather than pinned at import —
# the active profile is chosen by FPA_DOMAIN, which may switch at runtime. The
# PII, injection, and determination guards below stay here because they are
# cross-domain safety, not domain content.
#
# Backward-compat: OUT_OF_SCOPE_PATTERNS resolves to the live profile's
# scope_topics on each access for callers/tests that read it as a constant.
def __getattr__(name: str):
    if name == "OUT_OF_SCOPE_PATTERNS":
        return domain.get_profile().scope_topics
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


INJECTION_PATTERNS = re.compile(
    r"(ignore (all |your |previous |prior )*(instructions|rules|prompts)|"
    r"system prompt|you are now|pretend (you are|to be)|jailbreak|"
    r"disregard.{0,20}(instructions|guidelines)|"
    r"(ignora|olvida|descarta).{0,20}(instrucciones|reglas)|di exactamente|"
    r"kalimutan.{0,30}(tagubilin|panuto)|balewalain.{0,30}(tagubilin|panuto))",
    re.I,
)

# Language detection now delegates to the confidence-bearing character-n-gram
# classifier in :mod:`assistant.langid` (en/es/tl + an honest "unsure"). The old
# two-regex EN/ES word-count heuristic could not represent uncertainty and
# silently misclassified short or code-switched questions; the classifier returns
# a confidence and an "unsure" verdict that :func:`check_input` acts on. This
# still only *picks the rider's language*; it never blocks an answer.


def detect_language(text: str) -> str:
    """Best-guess BCP-47 language tag for ``text`` (``str`` for existing callers).

    Delegates to :func:`assistant.langid.detect`, which maps an uncertain input
    to :data:`~assistant.langid.DEFAULT_LANGUAGE` ("en"). Callers that need the
    confidence or the uncertainty flag use :func:`detect_language_confident`.
    """
    lang, _confidence = langid.detect(text)
    return lang


def detect_language_confident(text: str) -> tuple[str, float, bool]:
    """Return ``(lang, confidence, unsure)`` for callers that want the margin.

    ``lang`` is the classifier's best guess (a real tag, e.g. ``"tl"``, even when
    unsure), ``confidence`` is the top-two margin in ``[0, 1]``, and ``unsure`` is
    ``True`` when that margin is below :data:`assistant.langid.UNSURE_MARGIN`.
    """
    result = langid.classify(text)
    return result.lang, result.confidence, result.unsure


# The rider-facing refusal *text* now lives in the gettext catalogs behind
# assistant.i18n.refusal_message (EN source + ES translation); this module keeps
# only the *detection* below. Translating the message must not weaken the guard,
# so the control flow in check_input is unchanged — it still detects, then picks
# the message in the rider's language.


@dataclass
class InputCheck:
    ok: bool
    flags: list[str] = field(default_factory=list)
    message: str | None = None
    #: A short rider-facing note, in the answer language, set only when language
    #: detection was *unsure* and the pipeline fell back to English. It never
    #: blocks the answer — a caller may surface it alongside the answer so the
    #: rider knows we guessed. ``None`` whenever detection was confident.
    notice: str | None = None


def check_input(question: str) -> InputCheck:
    lang, _confidence, unsure = detect_language_confident(question)
    # An uncertain detection must never block an answer: we proceed in English
    # (the assistant's source language) and attach a translated note rather than
    # refuse or silently pick a language. Refusal *messages* below still render in
    # the detected language when detection was confident.
    if unsure:
        answer_lang = langid.DEFAULT_LANGUAGE
        notice: str | None = language_uncertain_notice(get_translation(answer_lang))
    else:
        answer_lang = lang
        notice = None
    translation = get_translation(answer_lang)
    flags = [name for name, pat in PII_PATTERNS.items() if pat.search(question)]
    if flags:
        return InputCheck(
            ok=False,
            flags=[f"pii:{f}" for f in flags],
            message=refusal_message(translation, "pii"),
        )
    flags = [
        name for name, pat in domain.get_profile().scope_topics.items() if pat.search(question)
    ]
    if flags:
        return InputCheck(
            ok=False,
            flags=[f"scope:{f}" for f in flags],
            message=refusal_message(translation, "scope"),
        )
    if INJECTION_PATTERNS.search(question):
        return InputCheck(
            ok=False, flags=["injection"], message=refusal_message(translation, "injection")
        )
    return InputCheck(ok=True, flags=["lang:unsure"] if unsure else [], notice=notice)


# ── output guards ────────────────────────────────────────────────────────────

# Words that only intensify a verdict. They are listed so an emphatic form of a
# forbidden phrase is still the forbidden phrase, and they are deliberately all
# emphatic: adding a hedge here ("may", "might") would silence the guard on the
# wording it exists to permit.
#
# `do`/`does`/`did` are here because of #197. Until 2026-09-06 the intensifier
# list was `definitely|certainly|clearly` and the emphatic auxiliary was absent,
# so `guards` forbade "you do NOT qualify" (its own third pattern) while
# permitting "you DO qualify" — the affirmative verdict, which is the direction
# that hurts a rider. `refuse-033` published exactly that: "I cannot tell you
# that you qualify for the free VIP fare ... At age 79, you do qualify for a
# half-priced fare." The case's own `forbidden_content` list caught it and this
# guard did not, and this guard is the one that runs in front of a rider
# (`assistant.answer` redacts on it before the answer is returned), so the
# eval's tripwire was the only thing standing between that sentence and the
# rider. `you certainly do qualify` was unreachable for the same reason: the old
# group matched one adverb, not an adverb followed by the auxiliary.
_EMPHASIS = r"(?:definitely|certainly|clearly|absolutely|indeed|truly|really|do|does|did)"

# Phrases that decide eligibility. Hedged forms ("you may qualify") are fine and
# are protected by the negative lookbehinds/lookaheads below.
DETERMINATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(rf"\byou (?:{_EMPHASIS} )*(qualify|are eligible)\b", re.I),
    re.compile(r"\byou('re| are) (not )?(qualified|entitled)\b", re.I),
    re.compile(r"\byou (do not|don't|won't|will not) qualify\b", re.I),
    re.compile(r"\byou are not eligible\b", re.I),
    re.compile(r"\bI (can )?(confirm|guarantee) (that )?you\b", re.I),
    # "usted sí que califica" is the Spanish emphatic and was reachable past the
    # narrower "(sí )?" for the same reason "you do qualify" was (#197).
    re.compile(r"\busted (?:sí (?:que )?)?(califica|es elegible)\b", re.I),
    re.compile(r"\busted no (califica|es elegible)\b", re.I),
    re.compile(r"\b(kwalipikado|karapat-dapat) ka\b", re.I),
    re.compile(r"\bmay (?:senior )?(?:diskwento|discount) para sa iyo\b", re.I),
]

# Contexts that legitimize an otherwise-matching phrase when they directly
# precede it: hedges ("you may qualify if…") and negated meta-statements
# ("I can't tell you that you qualify" — eval case refuse-001). Plain
# "I can tell you that you qualify" stays forbidden: the meta lead-in only
# counts when negated.
_QUOTE = "[\"'“”‘’«]*"
_HEDGE_BEFORE = re.compile(
    r"((may|might|could|can|whether|if|si|podría|puede(n)? que)\s+"
    r"|(can'?t|cannot|won'?t|wouldn'?t|unable to|not going to)\s+(just\s+)?"
    r"(say|tell( you)?|confirm|guarantee|state|declare|determine|decide)"
    rf"( that| whether| if)?[:,]?\s*{_QUOTE}"
    r"|(do(es)? not|doesn'?t|don'?t|won'?t|cannot|can'?t)\s+"
    r"(automatically\s+|necessarily\s+)?(mean|guarantee|imply|ensure)( that)?\s+"
    r"|(verif(y|ies|ying)|determin(e|es|ing)|decid(e|es|ing)|assess(es)?)"
    r"( whether| if| that)?\s+"
    rf"|no puedo (decirle?|confirmarle?|garantizarle?)( que)?[:,]?\s*{_QUOTE}"
    r"|no (significa|garantiza) que\s+"
    rf"|hindi ko (masasabi|makukumpirma|matitiyak)( na)?[:,]?\s*{_QUOTE}"
    r"|hindi (awtomatikong )?(nangangahulugan|tumitiyak) na\s+"
    r")$",
    re.I,
)


def find_determination_language(text: str) -> list[str]:
    """Return the determination phrases present in `text`, hedge-aware."""
    hits = []
    for pat in DETERMINATION_PATTERNS:
        for m in pat.finditer(text):
            prefix = text[max(0, m.start() - 40) : m.start()]
            if _HEDGE_BEFORE.search(prefix):
                continue
            hits.append(m.group(0))
    return hits


def redact_determination_language(text: str) -> str:
    """Drop only the sentences containing determination language.

    Enforcement at sentence granularity: a model answer that explains the
    published criteria but also quotes a forbidden phrase keeps its useful,
    cited content (eval case refuse-001). The caller re-checks the result and
    falls back to a full refusal if redaction wasn't clean.
    """
    segments = re.split(r"(?<=[.!?])\s+|\n", text)
    kept = [s for s in segments if s and not find_determination_language(s)]
    return "\n".join(kept).strip()


# Matches each doc-id in both single ``[doc:mst-fares]`` and combined
# ``[doc:mst-fares, doc:mst-fares-benefits]`` citation tags — the model writes
# the combined form when one claim draws on several passages.
#
# ``CITATION_RE`` remains the token-level expression used by existing callers.
# A rider-facing answer, however, must use a complete bracketed tag; otherwise
# prose such as ``doc:mst-fares`` or a broken ``[doc:mst-fares`` could satisfy
# the old presence-only guard without being a citation the UI can reliably
# remove and resolve.
CITATION_RE = re.compile(r"doc:([a-z0-9-]+)")
# Deliberately broader than the valid-tag grammar below. Once valid tags are
# removed, any remaining marker-looking token — including ``DOC:made-up`` or
# ``doc :made-up`` — is malformed and must fail closed.
CITATION_MARKER_RE = re.compile(r"\bdoc\s*:", re.I)
CITATION_TAG_RE = re.compile(r"\[doc:[a-z0-9-]+(?:,\s*doc:[a-z0-9-]+)*\]")


def extract_citation_ids(text: str) -> list[str]:
    """Return document IDs from complete, well-formed citation tags."""
    return [doc_id for tag in CITATION_TAG_RE.findall(text) for doc_id in CITATION_RE.findall(tag)]


def has_malformed_citation(text: str) -> bool:
    """True when a ``doc:`` token occurs outside a valid citation tag."""
    without_valid_tags = CITATION_TAG_RE.sub("", text)
    return bool(CITATION_MARKER_RE.search(without_valid_tags))


# English, Spanish, and Tagalog renderings of the "as of <date>" disclosure. The model
# phrases the Spanish one several ways ("políticas publicadas al 12 de junio…"),
# all anchored on "publicado/publicadas" (eval cases ml-003…ml-012).
AS_OF_RE = re.compile(
    r"\b(as of|published as of|publicad[oa]s?|a partir del?|"
    r"vigente[s]? (al|desde)|actualizad[oa]s? (al|el)|"
    r"inilathala noong|(?:mga )?patakaran(?:g)? (?:na )?inilathala noong|"
    r"batay sa (?:mga )?patakaran(?:g)? (?:na )?inilathala)\b",
    re.I,
)


# A positive verification handoff routes an eligibility-adjacent answer to where
# the decision actually happens — the agency or Cal-ITP Benefits — and how the
# rider starts (verify, apply, or contact). This is the constructive other half
# of the no-determination rule: the assistant never rules on the rider, and an
# eligibility answer never stops at the criterion; it names the next step toward
# an official decision. English and Spanish, mirrored, so the eval check that
# enforces it (evals/checks.py) reads both languages.
VERIFICATION_HANDOFF_RE = re.compile(
    r"(verif(y|ies|ication)\b|to verify|"
    r"eligibility (is |can be )?(verified|determined|decided)|"
    r"appl(y|ication|ies)\b|courtesy card|mobility pass|reduced[- ]fare (photo )?id|"
    r"cal-itp|customer service|"
    r"contact (the )?(agency|mst|sbmtd|yolobus|sacrt|humboldt|hta|transit)|"
    r"the agency (decides|determines|will decide|verifies)|"
    r"verificar|verificaci[óo]n|elegibilidad (se )?(verifica|determina|decide)|"
    r"solicit(ar|e|ud|a)\b|tarjeta de cortesía|pase de movilidad|"
    r"servicio al cliente|comun[ií]quese|la agencia (decide|determina|verifica))",
    re.I,
)


def find_verification_handoff(text: str) -> bool:
    """True if `text` routes the rider toward an official eligibility decision —
    verify, apply, get the card/ID, or contact the agency or Cal-ITP. Used by the
    RR4 eval check so an eligibility answer is never allowed to end on the bare
    criterion; see VERIFICATION_HANDOFF_RE for the patterns and rationale.
    """
    return bool(VERIFICATION_HANDOFF_RE.search(text))


@dataclass
class OutputCheck:
    ok: bool
    flags: list[str] = field(default_factory=list)


def check_output(text: str, *, require_citation: bool = True) -> OutputCheck:
    flags = []
    hits = find_determination_language(text)
    if hits:
        flags.append(f"determination_language:{'; '.join(hits)}")
    citation_ids = extract_citation_ids(text)
    if require_citation and not citation_ids:
        flags.append("malformed_citation" if has_malformed_citation(text) else "missing_citation")
    elif has_malformed_citation(text):
        flags.append("malformed_citation")
    return OutputCheck(ok=not flags, flags=flags)
