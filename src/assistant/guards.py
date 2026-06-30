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

from assistant import domain

# ── input guards ─────────────────────────────────────────────────────────────

PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    "phone": re.compile(r"\b(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"),
    "dob": re.compile(
        r"\b(?:born on|date of birth|birthday is|dob)\b.{0,20}\d", re.I
    ),
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

# Lightweight language detection; enough to answer refusals in the rider's
# language and to power the eval language-match check.
_ES_MARKERS = re.compile(
    r"\b(el|la|los|las|un|una|de|del|que|para|por|con|usted|cu[áa]nto|c[óo]mo|"
    r"tarifa|pasaje|descuento|reducida?|años|puede|debe|gratis|cuesta|necesito)\b",
    re.I,
)
_EN_MARKERS = re.compile(
    r"\b(the|a|an|of|that|for|with|you|is|do|how|what|fare|discount|reduced|"
    r"years|may|must|free|costs?)\b",
    re.I,
)


def detect_language(text: str) -> str:
    es = len(_ES_MARKERS.findall(text))
    en = len(_EN_MARKERS.findall(text))
    return "es" if es > en else "en"


_REFUSAL_MESSAGES = {
    "pii": {
        "en": (
            "Please don't share personal details like ID numbers, contact "
            "information, or birth dates — I don't need them to explain fare "
            "policy. Ask again without the personal details, or contact the "
            "transit agency's customer service directly."
        ),
        "es": (
            "Por favor no comparta datos personales como números de "
            "identificación, información de contacto o fechas de nacimiento; no "
            "los necesito para explicar la política de tarifas. Pregunte de "
            "nuevo sin los datos personales, o comuníquese con el servicio al "
            "cliente de la agencia de tránsito."
        ),
    },
    "scope": {
        "en": (
            "That's outside what I can help with. I can explain published fare "
            "and reduced-fare policies; for medical, immigration, or legal "
            "matters, please contact the transit agency directly or a qualified "
            "professional."
        ),
        "es": (
            "Eso está fuera de lo que puedo responder. Puedo explicar las "
            "políticas de tarifas publicadas; para asuntos médicos, migratorios "
            "o legales, comuníquese con la agencia de tránsito directamente o "
            "con un profesional calificado."
        ),
    },
    "injection": {
        "en": (
            "I can only answer questions about published transit fare policies. "
            "If you need other help, please contact the transit agency's "
            "customer service."
        ),
        "es": (
            "Solo puedo responder preguntas sobre las políticas de tarifas "
            "publicadas. Si necesita otra ayuda, comuníquese con el servicio al "
            "cliente de la agencia de tránsito."
        ),
    },
}


@dataclass
class InputCheck:
    ok: bool
    flags: list[str] = field(default_factory=list)
    message: str | None = None


def check_input(question: str) -> InputCheck:
    lang = detect_language(question)
    flags = [name for name, pat in PII_PATTERNS.items() if pat.search(question)]
    if flags:
        return InputCheck(
            ok=False,
            flags=[f"pii:{f}" for f in flags],
            message=_REFUSAL_MESSAGES["pii"][lang],
        )
    flags = [name for name, pat in OUT_OF_SCOPE_PATTERNS.items() if pat.search(question)]
    if flags:
        return InputCheck(
            ok=False,
            flags=[f"scope:{f}" for f in flags],
            message=_REFUSAL_MESSAGES["scope"][lang],
        )
    if INJECTION_PATTERNS.search(question):
        return InputCheck(
            ok=False, flags=["injection"], message=_REFUSAL_MESSAGES["injection"][lang]
        )
    return InputCheck(ok=True)


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
