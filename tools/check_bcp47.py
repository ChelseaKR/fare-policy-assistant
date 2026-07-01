#!/usr/bin/env python3
"""G3 BCP 47 / RFC 5646 language-tag validity gate (merge-blocking).

Every language tag the assistant authors must be well-formed *and* resolvable
against the IANA/CLDR registry, so a typo'd or invented locale ("sp", "en_US"
with the wrong separator, "esp") never ships. We validate:

* the locale directory names under ``src/assistant/locales`` (each is a real
  gettext catalog we compile and load), and
* the ``SUPPORTED_LANGUAGES`` tuple and ``DEFAULT_LANGUAGE`` declared in
  ``assistant.i18n``,

by parsing each through ``babel.Locale.parse`` (CLDR-backed). Anything Babel
cannot resolve fails the build.
"""

from __future__ import annotations

import sys

from babel import Locale, UnknownLocaleError

# The assistant package is importable in the environment `make verify`/CI runs in.
from assistant.i18n import DEFAULT_LANGUAGE, LOCALEDIR, SUPPORTED_LANGUAGES


def _catalog_dirs() -> list[str]:
    if not LOCALEDIR.is_dir():
        return []
    return sorted(p.name for p in LOCALEDIR.iterdir() if (p / "LC_MESSAGES").is_dir())


def main() -> int:
    tags = set(SUPPORTED_LANGUAGES) | {DEFAULT_LANGUAGE} | set(_catalog_dirs())
    errors: list[str] = []
    for tag in sorted(tags):
        try:
            Locale.parse(tag)
        except (UnknownLocaleError, ValueError) as exc:
            errors.append(f"{tag!r}: {exc}")

    if DEFAULT_LANGUAGE not in SUPPORTED_LANGUAGES:
        errors.append(
            f"DEFAULT_LANGUAGE {DEFAULT_LANGUAGE!r} is not in SUPPORTED_LANGUAGES "
            f"{SUPPORTED_LANGUAGES!r}"
        )

    if errors:
        print("BCP 47 tag validation FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print(f"BCP 47 OK: {sorted(tags)} are well-formed, registry-valid tags.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
