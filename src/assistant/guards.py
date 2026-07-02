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
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    "phone": re.compile(r"\b(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"),
    "dob": re.compile(r"\b(?:born on|date of birth|birthday is|dob)\b.{0,20}\d", re.I),
    "medicare_id": re.compile(r"\b\d[A-Z]\d{2}-?[A-Z]\d{2}-?[A-Z]{2}\d{2}\b", re.I),
}

# Topics adjacent to the domain that the assistant must redirect, not answer.
# Domain-specific, so sourced from the active profile (src/assistant/domain.py);
# the PII, injection, and determination guards below stay here because they are
# cross-domain safety, not domain content.
OUT_OF_SCOPE_PATTERNS: dict[str, re.Pattern[str]] = domain.get_profile().scope_topics

INJECTION_PATTERNS = re.compile(
    r"(ignore (all |your |previous |prior )*(instructions|rules|prompts)|"
    r"system prompt|you are now|pretend (you are|to be)|jailbreak|"
    r"disregard.{0,20}(instructions|guidelines)|"
    r"(ignora|olvida|descarta).{0,20}(instrucciones|reglas)|di exactamente)",
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
    flags = [name for name, pat in OUT_OF_SCOPE_PATTERNS.items() if pat.search(question)]
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

# Phrases that decide eligibility. Hedged forms ("you may qualify") are fine and
# are protected by the negative lookbehinds/lookaheads below.
DETERMINATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\byou (definitely |certainly |clearly )?(qualify|are eligible)\b", re.I),
    re.compile(r"\byou('re| are) (not )?(qualified|entitled)\b", re.I),
    re.compile(r"\byou (do not|don't|won't|will not) qualify\b", re.I),
    re.compile(r"\byou are not eligible\b", re.I),
    re.compile(r"\bI (can )?(confirm|guarantee) (that )?you\b", re.I),
    re.compile(r"\busted (sí )?(califica|es elegible)\b", re.I),
    re.compile(r"\busted no (califica|es elegible)\b", re.I),
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
# the combined form when one claim draws on several passages, and the earlier
# single-id-only pattern saw zero citations there and tripped the missing-
# citation guard on a perfectly grounded answer (eval case fresh-001).
CITATION_RE = re.compile(r"doc:([a-z0-9-]+)")
# English and Spanish renderings of the "as of <date>" disclosure. The model
# phrases the Spanish one several ways ("políticas publicadas al 12 de junio…"),
# all anchored on "publicado/publicadas" (eval cases ml-003…ml-012).
AS_OF_RE = re.compile(
    r"\b(as of|published as of|publicad[oa]s?|a partir del?|"
    r"vigente[s]? (al|desde)|actualizad[oa]s? (al|el))\b",
    re.I,
)


@dataclass
class OutputCheck:
    ok: bool
    flags: list[str] = field(default_factory=list)


def check_output(text: str, *, require_citation: bool = True) -> OutputCheck:
    flags = []
    hits = find_determination_language(text)
    if hits:
        flags.append(f"determination_language:{'; '.join(hits)}")
    if require_citation and not CITATION_RE.search(text):
        flags.append("missing_citation")
    return OutputCheck(ok=not flags, flags=flags)
