#!/usr/bin/env python3
"""Count what a mutmut run actually scored, and refuse when it scored nothing.

For five weekly runs (2026-08-10 to 2026-09-07) the advisory mutation job
crashed before checking a single mutant and still reported success, while
`docs/mutation-testing.md` went on publishing a ~75% score. A mutation score
over an empty set is the vacuous-pass shape: there is no number to report, so
this exits non-zero instead of printing one (issue #246).

It reads the ``*.meta`` files mutmut 3 writes beside each mutated module under
``mutants/``. Each holds ``exit_code_by_key``: one entry per generated mutant,
whose value is the test run's exit code, or ``null`` when the mutant was never
checked. The exit-code meanings mirror mutmut's own ``status_by_exit_code``.

The table it prints is dated and names the commit, so a copy pasted into the
docs says when it was true. Under GitHub Actions it is also appended to the
job summary. Standard library only, so it runs on a bare ``python3``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: mutmut 3.x ``status_by_exit_code``; anything unlisted is "suspicious" there too.
STATUS_BY_EXIT_CODE: dict[int | None, str] = {
    1: "killed",
    3: "killed",
    0: "survived",
    5: "no tests",
    2: "check was interrupted by user",
    None: "not checked",
    33: "no tests",
    34: "skipped",
    35: "suspicious",
    36: "timeout",
    37: "caught by type check",
    -24: "timeout",
    24: "timeout",
    152: "timeout",
    255: "timeout",
    -11: "segfault",
    -9: "segfault",
}


class NoMutationScore(Exception):
    """The run produced nothing to score, so no score is reported."""


@dataclass(frozen=True)
class Tally:
    by_status: Counter[str]
    survived_by_module: dict[str, int] = field(default_factory=dict)

    @property
    def generated(self) -> int:
        return sum(self.by_status.values())

    @property
    def killed(self) -> int:
        return self.by_status["killed"] + self.by_status["caught by type check"]

    @property
    def survived(self) -> int:
        return self.by_status["survived"]


def tally(mutants: Path) -> Tally:
    metas = sorted(mutants.rglob("*.meta")) if mutants.is_dir() else []
    if not metas:
        raise NoMutationScore(f"no mutmut results under {mutants}: mutmut never got that far")
    by_status: Counter[str] = Counter()
    survived_by_module: Counter[str] = Counter()
    for meta in metas:
        codes = json.loads(meta.read_text(encoding="utf-8")).get("exit_code_by_key", {})
        module = meta.name.removesuffix(".py.meta")
        for code in codes.values():
            status = STATUS_BY_EXIT_CODE.get(code, "suspicious")
            by_status[status] += 1
            if status == "survived":
                survived_by_module[module] += 1
    result = Tally(by_status=by_status, survived_by_module=dict(survived_by_module))
    if result.generated == 0:
        raise NoMutationScore("mutmut generated zero mutants; a score over nothing is not a score")
    unchecked = by_status["not checked"]
    if unchecked == result.generated:
        raise NoMutationScore(
            f"none of the {result.generated} mutants was checked; "
            "the test run crashed before scoring any of them"
        )
    return result


def render(result: Tally, *, date: str, commit: str) -> str:
    survived_detail = ", ".join(
        f"{module}.py {count}" for module, count in sorted(result.survived_by_module.items())
    )
    other = {
        status: count
        for status, count in sorted(result.by_status.items())
        if status not in {"killed", "caught by type check", "survived"}
    }
    survived = f"{result.survived} ({survived_detail})" if survived_detail else str(result.survived)
    other_text = ", ".join(f"{status} {count}" for status, count in other.items()) or "0"
    score = round(100 * result.killed / result.generated)
    rows = [
        f"Mutation run measured {date} at commit {commit}.",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Mutants generated | {result.generated} |",
        f"| Killed | {result.killed} |",
        f"| Survived | {survived} |",
        f"| Other | {other_text} |",
        f"| Mutation score | {score}% ({result.killed}/{result.generated}) |",
    ]
    return "\n".join(rows) + "\n"


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mutants", type=Path, default=REPO_ROOT / "mutants")
    args = parser.parse_args(argv)
    try:
        result = tally(args.mutants)
    except NoMutationScore as exc:
        print(f"mutation: FAILED: {exc}", file=sys.stderr)
        return 1
    table = render(result, date=datetime.now(UTC).date().isoformat(), commit=_commit())
    print(table, end="")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(table)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
