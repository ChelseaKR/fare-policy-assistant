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

Pure standard library + Babel's PO reader; no network, deterministic.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from babel.messages.catalog import Catalog, Message
from babel.messages.pofile import read_po

LOCALES = Path(__file__).resolve().parent.parent / "src" / "assistant" / "locales"
POT = LOCALES / "messages.pot"
CATALOGS = ("en", "es", "tl")

_FIELD = re.compile(r"\{[^{}]*\}")


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

    # G6: every supported catalog has exactly the template's key set.
    for name, ids in ids_by_name.items():
        extra = ids - pot_ids
        if extra:
            errors.append(f"G6: {name} has msgids absent from the template: {sorted(extra)}")

    # Structural completeness: every template msgid present in each catalog.
    for name, ids in ids_by_name.items():
        missing = pot_ids - ids
        if missing:
            errors.append(
                f"G5: {name} is missing msgids present in the template: {sorted(missing)}"
            )

    # G5: every msgstr (each plural form) non-empty, placeholders preserved.
    for name, catalog in catalogs.items():
        for message in catalog:
            if not message.id:
                continue
            src_fields = _fields(_key(message))
            if isinstance(message.id, (tuple, list)):
                src_fields |= _fields(message.id[1])
                forms = message.string if isinstance(message.string, (tuple, list)) else ()
                if not forms or any(not s for s in forms):
                    errors.append(f"G5: {name} has an empty plural form for {_key(message)!r}")
                    continue
                for form in forms:
                    if _fields(form) != src_fields:
                        errors.append(
                            f"G5: {name} placeholder mismatch in plural {_key(message)!r}: "
                            f"{_fields(form)} != {src_fields}"
                        )
            else:
                target = message.string
                if not target:
                    errors.append(f"G5: {name} has an empty msgstr for {message.id!r}")
                    continue
                if _fields(target) != src_fields:
                    errors.append(
                        f"G5: {name} placeholder mismatch in {message.id!r}: "
                        f"{_fields(target)} != {src_fields}"
                    )

    if errors:
        print("catalog parity FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print(
        f"catalog parity OK: {len(pot_ids)} msgids across {', '.join(CATALOGS)}, "
        "key-parity + completeness + "
        "placeholder parity hold."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
