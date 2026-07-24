"""Gettext localization seam for the assistant's rider-facing fixed strings.

The assistant emits two kinds of natural-language text. Most of an *answered*
response is written by the model from retrieved passages and is not fixed text
this module owns. But a small set of **fixed, rider-facing strings** — the
input-guard refusals (PII / out-of-scope / prompt-injection) and the
"no published policy answers that" decline — were carried as a bespoke EN/ES
Python dict/branch. This module is the single migration seam onto GNU gettext
catalogs (INTERNATIONALIZATION-STANDARD §3): the *source string is the English
text itself*, extracted by ``pybabel`` into ``locales/messages.pot`` and
translated in ``locales/<lang>/LC_MESSAGES/messages.po``.

Deliberately **not** routed through this module, because the standard scopes i18n
to end-user text (not operator logs, model prompts, or retrieval internals):

* the model prompts under ``prompts/`` and the model's own answer text;
* the retrieval query-expansion synonyms in :mod:`assistant.retrieve` (that is
  retrieval logic, not a displayed string);
* the guard *detection* patterns in :mod:`assistant.guards` (PII / injection /
  determination / citation / as-of regexes) — those are control flow; migrating
  strings must not weaken them, so only the *displayed message text* moves here;
* the CLI (:mod:`assistant.cli`) and the web handler's HTTP/JSON error bodies
  (:mod:`web.handler`), which are English-only operator surfaces.
"""

from __future__ import annotations

import gettext
from pathlib import Path

#: gettext domain — the ``messages`` in ``messages.po`` / ``messages.mo``.
DOMAIN = "messages"

#: Compiled catalogs live beside this module (inside the package) so a checkout
#: or an installed wheel resolves them with no separate install step. See
#: docs/I18N.md for the decision to commit the compiled ``.mo`` files.
LOCALEDIR = Path(__file__).resolve().parent / "locales"

#: BCP 47 tags the assistant ships a fixed-string catalog for. English is the
#: source language and the fallback for any unsupported request.
#:
#: Tagalog is a stretch language over an English-only policy corpus, but its
#: safety/no-support strings have a complete catalog so a deterministic guard
#: never falls back to English after confidently detecting a Tagalog question.
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "es", "tl")
DEFAULT_LANGUAGE = "en"


def _parse_language_range(part: str, index: int) -> tuple[float, int, str] | None:
    token = part.strip()
    if not token:
        return None
    tag_part, _sep, params = token.partition(";")
    tag = tag_part.strip().lower()
    if not tag:
        return None
    weight = 1.0
    params = params.strip()
    if params.startswith("q="):
        try:
            weight = float(params[2:])
        except ValueError:
            weight = 0.0
    return weight, -index, tag


def _supported_language(tag: str) -> str | None:
    if tag == "*":
        return DEFAULT_LANGUAGE
    if tag in SUPPORTED_LANGUAGES:
        return tag
    primary = tag.split("-", 1)[0]
    return primary if primary in SUPPORTED_LANGUAGES else None


def get_translation(lang: str) -> gettext.NullTranslations:
    """Return the gettext catalog for ``lang``, falling back to English text.

    ``fallback=True`` means an unknown tag (or a missing ``.mo``) yields a
    :class:`gettext.NullTranslations` whose ``gettext`` returns the English
    source msgid unchanged — never an exception, never a blank string. The
    rider-facing guards call this with the language that
    :func:`assistant.guards.detect_language` inferred from the question.
    """
    return gettext.translation(DOMAIN, localedir=str(LOCALEDIR), languages=[lang], fallback=True)


def negotiate_lang(accept_language: str | None) -> str:
    """Resolve a supported language from an RFC 9110 ``Accept-Language`` value.

    The rider-facing language is chosen today from the *content* of the question
    (:func:`assistant.guards.detect_language`), not an HTTP header; this helper
    exists so a future negotiated surface reuses one fallback chain —
    ``<requested> → <primary subtag> → en`` (INTERNATIONALIZATION-STANDARD §6).
    Quality weights (``;q=``) are honored; an empty, malformed, or unmatched
    header falls back to English. Wiring it into the web handler's response
    negotiation (G11 ``Vary: Accept-Language``) is Phase 3, not this migration.
    """
    if not accept_language:
        return DEFAULT_LANGUAGE
    ranked = [
        parsed
        for index, part in enumerate(accept_language.split(","))
        if (parsed := _parse_language_range(part, index)) is not None
    ]
    for weight, _neg_index, tag in sorted(ranked, reverse=True):
        if weight <= 0.0:
            continue
        matched = _supported_language(tag)
        if matched is not None:
            return matched
    return DEFAULT_LANGUAGE


def refusal_message(translation: gettext.NullTranslations, kind: str) -> str:
    """Localized rider-facing refusal text for an input-guard category.

    ``kind`` is the guard category (``pii`` / ``scope`` / ``injection``). The
    control flow that *decides* to refuse — the PII, out-of-scope, and injection
    detection — lives in :mod:`assistant.guards` and is unchanged; this only
    localizes the message shown once a refusal has been chosen. The English
    strings here are the msgids extracted by ``pybabel``.
    """
    _ = translation.gettext
    messages: dict[str, str] = {
        "pii": _(
            "Please don't share personal details like ID numbers, contact "
            "information, or birth dates — I don't need them to explain fare "
            "policy. Ask again without the personal details, or contact the "
            "transit agency's customer service directly."
        ),
        "scope": _(
            "That's outside what I can help with. I can explain published fare "
            "and reduced-fare policies; for medical, immigration, or legal "
            "matters, please contact the transit agency directly or a qualified "
            "professional."
        ),
        "injection": _(
            "I can only answer questions about published transit fare policies. "
            "If you need other help, please contact the transit agency's "
            "customer service."
        ),
    }
    return messages[kind]


def language_uncertain_notice(translation: gettext.NullTranslations) -> str:
    """Localized "I wasn't sure of your language, answering in English" note.

    Attached by :func:`assistant.guards.check_input` when the n-gram classifier
    (:mod:`assistant.langid`) could not confidently identify the question's
    language — a short, ambiguous, or code-switched input. It is a *note*, not a
    refusal: the answer proceeds in English. Because detection was unsure the
    note itself is rendered in English (the fallback language); a caller that
    surfaces it keeps the rider informed that we guessed.
    """
    _ = translation.gettext
    return _(
        "I wasn't sure which language you were using, so I'm answering in "
        "English. If you'd like a different language, please ask again in that "
        "language."
    )


def no_support_message(
    translation: gettext.NullTranslations,
    *,
    agency_hint: str | None,
    statewide_info: str,
) -> str:
    """Localized rider-facing "no published policy answers that" decline.

    Mirrors the prior bespoke EN/ES branches exactly: an agency-specific pointer
    when the question named an agency, otherwise a pointer to the rider's own
    agency or the statewide info line. The no-determination stance ("I won't
    guess about fares or eligibility") is part of the translated *text*, not the
    control flow — the pipeline's decision to decline is unchanged.
    """
    _ = translation.gettext
    if agency_hint:
        where = _("the agency's website or customer service")
    else:
        where = _("your transit agency directly, or {statewide}").format(statewide=statewide_info)
    return _(
        "I don't have a published policy document that answers that, and I "
        "won't guess about fares or eligibility. Please check {where} for "
        "current information."
    ).format(where=where)
