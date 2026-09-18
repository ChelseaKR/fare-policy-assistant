#!/usr/bin/env python3
"""Is the site a visitor gets at evals.chelseakr.com the site this repository has?

On 2026-09-13 that site was serving a build from 2026-07-12 -- 63 days and 154
commits behind ``main``, rendered by a version of
``scripts/build_evidence_site.py`` that no longer exists and loading a
``trend.svg`` neither current renderer emits. An SEO audit measured the live
page, drew conclusions about ``main``, and had to retract them: the page was not
evidence about the code, it was evidence that the code had never been published.

Every gate in this repository was green throughout, because none of them was
asking. ``pages.yml`` ran four times a day the whole time.

This module is the clock nobody was watching. It deploys nothing, holds no
credential that could, and answers one question: how far behind ``main`` is the
commit the live site was built from.

Why the deployment record and not the run history
-------------------------------------------------
The obvious source is ``pages.yml``'s runs. It is the wrong one, and this repo
is the reason the distinction is not academic. Both nightly jobs sit behind
``vars.NIGHTLY_HUB_PUBLISH_ENABLED`` (ADR 0032), so the scheduled run reaches
``pages.yml`` four times a day, skips every job, and finishes. Its runs are the
only recent ones there are. Reading a run list gives one of two wrong answers:
count ``skipped`` as a deploy and the site reports as fresh every six hours
forever, or filter them out and there is no successful run in the window at all,
which is a refusal dressed up as a measurement.

The deployment record cannot say that. A ``github-pages`` deployment exists
because bytes were published, it names the commit they were built from, and its
status says whether the publish succeeded. There is exactly one in this
repository's history: ``c788f9efc``, 2026-07-12. That is the true answer and it
is the only place the true answer is written down.

The rule this file follows, taken from the failure above: a detector that cannot
tell must refuse, never report a comfortable zero. Every unmeasurable case below
raises `StalenessUnknown` rather than returning a number that would read as a
measurement.

Standard library only, and imports nothing from the rest of the repository, so
the sentinel runs on a bare ``python3`` with no dependency resolution -- the same
constraint `scripts/site_meta.py` documents, for the same reason.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_REPO = "ChelseaKR/fare-policy-assistant"

#: The Pages environment a deployment has to belong to. A repository can carry
#: deployments for other environments; only this one is the published site.
PAGES_ENVIRONMENT = "github-pages"

#: How long an unpublished visitor-visible commit may wait before this reports.
#: The site is a promotion artifact, not a continuous deploy, so the useful
#: threshold is generous -- it exists to catch two months, not two days.
DEFAULT_MAX_AGE_DAYS = 14

#: Paths whose change alters what a visitor receives. Everything the two
#: publishers read to render a page, and nothing else: the evaluation corpus,
#: the test suite and the ADRs all change `main` constantly without changing a
#: published byte, and counting them would make the number meaningless well
#: before it made it alarming.
#:
#: `.github/workflows/pages.yml` is on this list because the nightly renderer
#: *is* that file (ADR 0032) -- the one publisher whose source is a workflow.
SITE_SOURCE_PREFIXES = (
    "scripts/build_evidence_site.py",
    "scripts/site_meta.py",
    ".github/workflows/pages.yml",
    "docs/pages/",
    "docs/eval-history.svg",
)

_SHA = re.compile(r"^[0-9a-f]{40}$")


class StalenessUnknown(Exception):
    """The comparison could not be made, so no number is reported.

    Raised in preference to returning zero anywhere the inputs do not support a
    measurement. The caller turns this into a red run: a sentinel that cannot
    tell is a broken sentinel, and it has to look broken.
    """


@dataclass(frozen=True)
class DeployRecord:
    """The published build: which commit it came from, and when it went out."""

    deployment_id: int
    sha: str
    created_at: datetime


@dataclass(frozen=True)
class Drift:
    """How far the published build is behind `main`."""

    deployed: DeployRecord
    head: str
    days: int
    commits: int
    visitor_commits: int
    max_age_days: int

    @property
    def overdue(self) -> bool:
        """Report only when something a visitor would receive is waiting.

        Age alone is not the signal. A site that has not been republished for a
        month because nothing it publishes has changed is correct, not stale.
        """
        return self.visitor_commits > 0 and self.days > self.max_age_days


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def newest_successful_deployment(
    deployments: Iterable[Mapping[str, Any]],
    statuses_for: Any,
) -> DeployRecord:
    """The most recent `github-pages` deployment that actually published.

    `statuses_for` is called with a deployment id and returns that deployment's
    statuses. A deployment row is a *request* to publish; its statuses are what
    say whether bytes landed. A deployment whose newest status is `failure`,
    `error` or `in_progress` never became a site, and treating its commit as the
    live one would report the site as fresher than it is -- the precise direction
    of error this whole file exists to prevent.
    """
    candidates = [
        d
        for d in deployments
        if d.get("environment") in (None, PAGES_ENVIRONMENT) and _SHA.match(str(d.get("sha", "")))
    ]
    if not candidates:
        raise StalenessUnknown(
            "no github-pages deployment in this repository's history: there is no published "
            "build to compare main against"
        )
    candidates.sort(key=lambda d: _parse_timestamp(str(d["created_at"])), reverse=True)

    for deployment in candidates:
        states = [str(s.get("state", "")) for s in statuses_for(deployment["id"])]
        if states and states[0] == "success":
            return DeployRecord(
                deployment_id=int(deployment["id"]),
                sha=str(deployment["sha"]),
                created_at=_parse_timestamp(str(deployment["created_at"])),
            )

    raise StalenessUnknown(
        f"none of the {len(candidates)} github-pages deployment(s) reports a successful status: "
        "nothing here proves any build was ever published"
    )


def ships_to_visitors(path: str) -> bool:
    """Does changing this file change what the published site shows?"""
    return any(
        path == prefix if not prefix.endswith("/") else path.startswith(prefix)
        for prefix in SITE_SOURCE_PREFIXES
    )


def _run_git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _git(*args: str) -> str:
    result = _run_git(*args)
    if result.returncode != 0:
        raise StalenessUnknown(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _has_commit(sha: str) -> bool:
    """Whether this clone contains the commit, without raising on absence.

    `git cat-file` exits non-zero for a commit that is simply not here, which is
    the ordinary shallow-clone case and not a git failure. Routing it through
    `_git` would report it as one, and the refusal the caller raises -- the one
    that names the shallow checkout and says why a zero would be wrong -- would
    never be reached.
    """
    return _run_git("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def require_comparable(deployed_sha: str, head: str) -> None:
    """Refuse unless this clone can actually place the deployed commit on `main`.

    Both failures below report zero drift if they are not caught, and both are
    ordinary. A shallow checkout simply does not contain a commit from July, so
    `git log <deployed>..HEAD` lists nothing and the site reads as up to date --
    which is why the sentinel workflow checks out with `fetch-depth: 0` and why
    this refuses rather than trusting that it did. A force-push or a rebase
    leaves the deployed commit off `main` entirely, where "commits since" is not
    a question with an answer.
    """
    if not _SHA.match(deployed_sha):
        raise StalenessUnknown(f"deployed commit {deployed_sha!r} is not a commit id")
    if not _has_commit(deployed_sha):
        raise StalenessUnknown(
            f"deployed commit {deployed_sha[:9]} is not in this clone: the checkout is shallow, "
            "and a comparison against a history that does not reach the published build would "
            "report no drift at all"
        )
    merge_base = _git("merge-base", deployed_sha, head)
    if merge_base != _git("rev-parse", deployed_sha):
        raise StalenessUnknown(
            f"deployed commit {deployed_sha[:9]} is not an ancestor of {head}: the history has "
            "diverged and 'commits since the deploy' has no answer"
        )


def commits_between(deployed_sha: str, head: str) -> list[tuple[str, list[str]]]:
    """Each commit after the deployed one, with the paths it touched."""
    raw = _git("log", "--format=%x00%H", "--name-only", f"{deployed_sha}..{head}")
    commits: list[tuple[str, list[str]]] = []
    for block in raw.split("\x00"):
        block = block.strip("\n")
        if not block:
            continue
        lines = [line for line in block.splitlines() if line.strip()]
        commits.append((lines[0], lines[1:]))
    return commits


def measure(
    deployed: DeployRecord,
    head: str,
    now: datetime,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> Drift:
    """Place the published build against `main`, or refuse."""
    head_sha = _git("rev-parse", head)
    require_comparable(deployed.sha, head_sha)
    commits = commits_between(deployed.sha, head_sha)
    visitor = [c for c in commits if any(ships_to_visitors(p) for p in c[1])]
    return Drift(
        deployed=deployed,
        head=head_sha,
        days=(now - deployed.created_at).days,
        commits=len(commits),
        visitor_commits=len(visitor),
        max_age_days=max_age_days,
    )


def render(drift: Drift) -> str:
    """The report. States the measurement before its verdict, always."""
    lines = [
        f"Published build:  {drift.deployed.sha[:9]}  "
        f"({drift.deployed.created_at.date().isoformat()}, "
        f"deployment {drift.deployed.deployment_id})",
        f"main:             {drift.head[:9]}",
        f"Behind by:        {drift.days} days, {drift.commits} commits, "
        f"{drift.visitor_commits} of them changing what a visitor receives",
    ]
    if drift.overdue:
        lines.append(
            f"\nOVERDUE: {drift.visitor_commits} visitor-visible commit(s) have waited "
            f"{drift.days} days, past the {drift.max_age_days}-day threshold. "
            "The live site is not what this repository says it is."
        )
    elif drift.visitor_commits:
        lines.append(
            f"\nWaiting: {drift.visitor_commits} visitor-visible commit(s), "
            f"{drift.days} days, within the {drift.max_age_days}-day threshold."
        )
    else:
        lines.append("\nUp to date: nothing published has changed since the live build.")
    return "\n".join(lines)


def as_json(drift: Drift) -> dict[str, Any]:
    return {
        "deployed_sha": drift.deployed.sha,
        "deployed_at": drift.deployed.created_at.isoformat(),
        "deployment_id": drift.deployed.deployment_id,
        "head": drift.head,
        "days": drift.days,
        "commits": drift.commits,
        "visitor_commits": drift.visitor_commits,
        "overdue": drift.overdue,
    }


def _gh(path: str) -> Any:
    """Read the API through `gh`, which the runner already authenticates."""
    result = subprocess.run(
        ["gh", "api", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise StalenessUnknown(f"gh api {path} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--head", default="origin/main")
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--json", action="store_true", help="emit the measurement as JSON")
    parser.add_argument(
        "--deployments-json",
        type=Path,
        help="read deployments from a file instead of the API (offline use and tests)",
    )
    args = parser.parse_args(argv)

    try:
        if args.deployments_json:
            payload = json.loads(args.deployments_json.read_text(encoding="utf-8"))
            deployments = payload["deployments"]
            statuses = payload["statuses"]

            def statuses_for(deployment_id: Any) -> list[Mapping[str, Any]]:
                return statuses.get(str(deployment_id), [])
        else:
            deployments = _gh(
                f"repos/{args.repo}/deployments?environment={PAGES_ENVIRONMENT}&per_page=20"
            )

            def statuses_for(deployment_id: Any) -> list[Mapping[str, Any]]:
                return _gh(f"repos/{args.repo}/deployments/{deployment_id}/statuses?per_page=10")

        deployed = newest_successful_deployment(deployments, statuses_for)
        drift = measure(deployed, args.head, datetime.now(UTC), args.max_age_days)
    except StalenessUnknown as exc:
        print(f"cannot measure deploy staleness: {exc}", file=sys.stderr)
        _write_github_output(None, str(exc))
        return 2

    print(json.dumps(as_json(drift), indent=2) if args.json else render(drift))
    _write_github_output(drift, None)
    return 0


def _write_github_output(drift: Drift | None, error: str | None) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        if drift is None:
            handle.write("measured=false\n")
            handle.write(f"error={error or 'unknown'}\n")
        else:
            handle.write("measured=true\n")
            handle.write(f"overdue={str(drift.overdue).lower()}\n")
            handle.write(f"days={drift.days}\n")
            handle.write(f"commits={drift.commits}\n")
            handle.write(f"visitor_commits={drift.visitor_commits}\n")
            handle.write(f"deployed_sha={drift.deployed.sha}\n")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
