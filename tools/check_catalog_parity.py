#!/usr/bin/env python3
"""G6 supported-language key-parity + G5 completeness gate (merge-blocking).

Enforces, over ``src/assistant/locales``:

* **G6 key-parity** -- every supported catalog has the same msgid set and covers
  every msgid in ``messages.pot``. A key missing from any language fails the build.
* **G5 completeness** -- every msgstr (each plural form) is non-empty. The
  translations are complete, so completeness is enforced as a hard gate rather
  than deferred.
* **G5 placeholder parity** -- the set of ``{...}`` fields is identical between
  each msgid and its translation, so a rename or dropped ``{statewide}`` /
  ``{where}`` cannot ship (a broken placeholder would raise at ``.format`` time
  in a rider-facing refusal -- exactly the failure this guards).
* **identity translation** (repo-local, not one of the numbered standard gates)
  -- ``en`` is the source language, so its msgstr must *equal* its msgid; every
  other catalog's msgstr must *differ* from it, unless the msgid is
  untranslatable by construction or the pair is listed in
  ``identical_by_design.json`` with a reason. See ``_identity_errors``.

Pure standard library + Babel's PO reader; no network, deterministic.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from babel.messages.catalog import Catalog, Message
from babel.messages.pofile import read_po

LOCALES = Path(__file__).resolve().parent.parent / "src" / "assistant" / "locales"
POT = LOCALES / "messages.pot"
CATALOGS = ("en", "es", "tl")

#: The language the msgids are written in. `docs/I18N.md`: "the source string is
#: the English text itself", so `en`'s catalog is an identity map by
#: construction and every other catalog is a translation of it.
SOURCE_LOCALE = "en"

#: Reasoned exemptions from the differ-from-source rule, for msgids that are
#: legitimately identical in a target language and that the two mechanical
#: classes below do not cover -- a product name, say, or a proper noun.
EXEMPTIONS = LOCALES / "identical_by_design.json"

_FIELD = re.compile(r"\{[^{}]*\}")

#: A single token of capitals, digits and code punctuation: `OK`, `CSV`, `PDF`,
#: `GTFS`, `WCAG`, `TK-12`. Deliberately requires upper case, so an ordinary
#: short English word (`Help`, `Fares`) is NOT exempt and has to be translated
#: or listed with a reason. Deliberately caps the length, so a shouted sentence
#: fragment does not slip through.
_CODE_TOKEN = re.compile(r"[A-Z0-9][A-Z0-9./+-]{1,7}")


def _load(path: Path, locale: str | None) -> Catalog:
    with path.open("rb") as fh:
        return read_po(fh, locale=locale)


def _key(message: Message) -> str:
    """A hashable identity for a message (the singular msgid for plurals)."""
    return message.id[0] if isinstance(message.id, (tuple, list)) else message.id


def _ids(catalog: Catalog) -> set[str]:
    return {_key(m) for m in catalog if m.id}


def _fields(text: str) -> set[str]:
    return set(_FIELD.findall(text))


def _plural_message_errors(name: str, message: Message) -> list[str]:
    """G5 for a pluralizable msgid: every form non-empty, placeholders preserved."""
    src_fields = _fields(_key(message)) | _fields(message.id[1])
    forms = message.string if isinstance(message.string, (tuple, list)) else ()
    if not forms or any(not s for s in forms):
        return [f"G5: {name} has an empty plural form for {_key(message)!r}"]
    return [
        f"G5: {name} placeholder mismatch in plural {_key(message)!r}: "
        f"{_fields(form)} != {src_fields}"
        for form in forms
        if _fields(form) != src_fields
    ]


def _singular_message_errors(name: str, message: Message) -> list[str]:
    """G5 for a non-pluralizable msgid: msgstr non-empty, placeholders preserved."""
    target = message.string
    if isinstance(target, (tuple, list)):
        # Babel types `Message.string` as str-or-sequence, and a catalog that
        # carries plural msgstrs under a non-plural msgid is malformed rather
        # than merely oddly typed: `gettext()` would return the wrong shape to
        # a rider-facing `.format()`. Report it instead of narrowing it away.
        return [
            f"G5: {name} has plural msgstrs under the non-plural msgid {message.id!r}; "
            "re-extract with `make i18n`"
        ]
    if not target:
        return [f"G5: {name} has an empty msgstr for {message.id!r}"]
    src_fields = _fields(_key(message))
    if _fields(target) != src_fields:
        return [
            f"G5: {name} placeholder mismatch in {message.id!r}: {_fields(target)} != {src_fields}"
        ]
    return []


def _completeness_errors(name: str, catalog: Catalog) -> list[str]:
    """G5 over one catalog: every msgstr non-empty with placeholders preserved."""
    errors: list[str] = []
    for message in catalog:
        if not message.id:
            continue
        if isinstance(message.id, (tuple, list)):
            errors += _plural_message_errors(name, message)
        else:
            errors += _singular_message_errors(name, message)
    return errors


def _strings(message: Message) -> tuple[str, ...]:
    """Every msgstr this message carries, as a tuple (one entry if singular).

    A missing msgstr becomes ``""``, which no msgid equals, so an untranslated
    row is reported by the completeness check above and not a second time here.
    """
    if isinstance(message.string, (tuple, list)):
        return tuple(form or "" for form in message.string)
    return (message.string or "",)


def _sources(message: Message) -> tuple[str, ...]:
    """Every source form this message carries: (singular,) or (singular, plural)."""
    if isinstance(message.id, (tuple, list)):
        return tuple(message.id)
    return (message.id,)


def _is_identical_to_source(message: Message) -> bool:
    """True when the translation adds no text the source did not already have.

    For a plural message this is deliberately "every form is one of the source
    forms" rather than a positional comparison: a locale may declare a different
    number of plural forms than English does, and a catalog that fills all of
    them with English is untranslated whichever way round they landed.
    """
    return set(_strings(message)) <= set(_sources(message))


def untranslatable_reason(msgid: str) -> str | None:
    """Why this msgid is legitimately the same in every language, or ``None``.

    Two mechanical classes only. Both are about the msgid having no prose in it
    to translate, which is checkable; neither is about a *word* being the same
    in two languages, which is not.

    A blanket "a non-English msgstr must differ from its msgid" rule is wrong,
    and wrong in the direction that gets a gate switched off: plenty of strings
    are correctly identical. But an over-wide exemption is worse than no gate,
    because it reads as coverage. So anything outside these two classes -- a
    product name, a proper noun, a borrowed word -- takes a written reason in
    ``identical_by_design.json`` instead of a pattern.
    """
    bare = _FIELD.sub(" ", msgid).strip()
    if not any(char.isalpha() for char in bare):
        return "no alphabetic content once the placeholders are removed"
    if len(bare.split()) == 1:
        if "://" in bare:
            return "a bare URL"
        if _CODE_TOKEN.fullmatch(bare):
            return "a single all-caps code token (acronym, format name, or identifier)"
    return None


def _load_exemptions(path: Path) -> tuple[dict[tuple[str, str], str], list[str]]:
    """Read the reasoned exemption list, or report why it cannot be read.

    A missing file is fine and means "no exemptions"; a malformed one is an
    error rather than a silent empty list, because an exemption file that fails
    open turns the check it guards into one that cannot fail.
    """
    if not path.is_file():
        return {}, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, [f"identity: {path.name} could not be read as JSON: {exc}"]
    if not isinstance(payload, dict) or not isinstance(payload.get("identical_by_design"), list):
        return {}, [f"identity: {path.name} must be an object with an identical_by_design list"]

    exemptions: dict[tuple[str, str], str] = {}
    errors: list[str] = []
    for index, entry in enumerate(payload["identical_by_design"]):
        if not isinstance(entry, dict):
            errors.append(f"identity: {path.name} entry {index} is not an object")
            continue
        locale, msgid, reason = entry.get("locale"), entry.get("msgid"), entry.get("reason")
        if not isinstance(locale, str) or not isinstance(msgid, str):
            errors.append(f"identity: {path.name} entry {index} needs a locale and a msgid")
            continue
        if not isinstance(reason, str) or not reason.strip():
            errors.append(
                f"identity: {path.name} exempts {locale}/{msgid!r} with no reason. "
                "An exemption without a written reason is the gate switched off for that row"
            )
            continue
        exemptions[(locale, msgid)] = reason
    return exemptions, errors


def _identity_errors(
    catalogs: dict[str, Catalog],
    exemptions: dict[tuple[str, str], str],
) -> list[str]:
    """The source catalog must match its msgids; the others must not.

    The gap this closes, stated plainly: key parity, completeness and
    placeholder parity are all satisfied by a Spanish catalog that is verbatim
    English. Its keys match, its msgstrs are non-empty, and its placeholders are
    trivially identical. Nothing here or anywhere else in `make i18n` was
    looking at whether any translating had happened.
    """
    errors: list[str] = []
    targets = [name for name in catalogs if name != SOURCE_LOCALE]
    if not targets:
        # The same shape as the empty-template floor below: with no target
        # locale to compare, this check reports "identity holds" over nothing.
        return [
            f"identity: no catalog other than {SOURCE_LOCALE!r} was checked, so the "
            "differ-from-source rule ran against nothing. Add the target locales to "
            "CATALOGS or remove this check with them"
        ]

    source = catalogs.get(SOURCE_LOCALE)
    if source is not None:
        for message in source:
            if not message.id or _is_identical_to_source(message):
                continue
            errors.append(
                f"identity: the {SOURCE_LOCALE} msgstr for {_key(message)!r} differs from its "
                "msgid. The source catalog is an identity map by construction (docs/I18N.md: "
                "'the source string is the English text itself'); edit the source string and "
                "re-extract instead"
            )

    for name in targets:
        for message in catalogs[name]:
            if not message.id or not _is_identical_to_source(message):
                continue
            key = _key(message)
            if untranslatable_reason(key) is not None:
                continue
            if (name, key) in exemptions:
                continue
            errors.append(
                f"identity: {name}'s msgstr for {key!r} is byte-identical to the English "
                "msgid, which every other check in this gate accepts. Translate it, or -- if "
                f"it is genuinely the same in {name} -- add it to {EXEMPTIONS.name} with a reason"
            )
    return errors


def _stale_exemption_errors(
    catalogs: dict[str, Catalog],
    pot_ids: set[str],
    exemptions: dict[tuple[str, str], str],
) -> list[str]:
    """An exemption that is not currently doing anything must be deleted.

    Without this the list only ever grows, and a list that only grows stops
    describing the catalogs and starts describing the project's history.
    """
    errors: list[str] = []
    for locale, msgid in sorted(exemptions):
        if locale not in catalogs:
            errors.append(
                f"identity: {EXEMPTIONS.name} exempts locale {locale!r}, which is not a "
                f"checked catalog ({', '.join(catalogs)})"
            )
            continue
        if locale == SOURCE_LOCALE:
            errors.append(
                f"identity: {EXEMPTIONS.name} exempts the source locale {locale!r}, where "
                "identity is required rather than excused"
            )
            continue
        if msgid not in pot_ids:
            errors.append(
                f"identity: {EXEMPTIONS.name} exempts {locale}/{msgid!r}, which the template "
                "no longer declares. Remove the entry"
            )
            continue
        message = catalogs[locale].get(msgid)
        if message is None or not _is_identical_to_source(message):
            errors.append(
                f"identity: {EXEMPTIONS.name} exempts {locale}/{msgid!r}, which is now "
                "translated. Remove the entry"
            )
    return errors


def _key_parity_errors(pot_ids: set[str], ids_by_name: dict[str, set[str]]) -> list[str]:
    """G6 both ways: no catalog invents a msgid, none is missing one."""
    errors: list[str] = []
    for name, ids in ids_by_name.items():
        extra = ids - pot_ids
        if extra:
            errors.append(f"G6: {name} has msgids absent from the template: {sorted(extra)}")
    for name, ids in ids_by_name.items():
        missing = pot_ids - ids
        if missing:
            errors.append(
                f"G5: {name} is missing msgids present in the template: {sorted(missing)}"
            )
    return errors


def main() -> int:
    errors: list[str] = []

    pot = _load(POT, None)
    catalogs = {
        name: _load(LOCALES / name / "LC_MESSAGES" / "messages.po", name) for name in CATALOGS
    }
    pot_ids = _ids(pot)
    ids_by_name = {name: _ids(catalog) for name, catalog in catalogs.items()}

    # The gate's own denominator. Every check below iterates over `pot_ids`, so
    # an empty template makes all of them vacuous and this prints "catalog
    # parity OK: 0 msgids" while nothing rider-facing is translated at all.
    # G2-lite catches a template that *drifts* from the sources, but a commit
    # that empties the sources and the template together drifts from nothing.
    # A floor is the cheap fix: the six strings docs/I18N.md enumerates are the
    # whole translated surface, so the template can never legitimately be empty.
    if not pot_ids:
        errors.append(
            "denominator: messages.pot declares no msgids, which makes every check below "
            "vacuous. Re-extract with `make i18n`; if the rider-facing strings really were "
            "removed, this gate has nothing left to protect and should be removed with them"
        )

    errors += _key_parity_errors(pot_ids, ids_by_name)
    for name, catalog in catalogs.items():
        errors += _completeness_errors(name, catalog)

    exemptions, exemption_errors = _load_exemptions(EXEMPTIONS)
    errors += exemption_errors
    errors += _identity_errors(catalogs, exemptions)
    errors += _stale_exemption_errors(catalogs, pot_ids, exemptions)

    if errors:
        print("catalog parity FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    targets = [name for name in CATALOGS if name != SOURCE_LOCALE]
    print(
        f"catalog parity OK: {len(pot_ids)} msgids across {', '.join(CATALOGS)}, "
        "key-parity + completeness + "
        "placeholder parity hold; "
        f"{', '.join(targets)} differ from the {SOURCE_LOCALE} source "
        f"({len(exemptions)} reasoned exemptions)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
