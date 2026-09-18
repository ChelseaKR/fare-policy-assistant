"""The detector that answers "is the live site the site this repository has?".

Written from both directions, because the failure this replaces was a green
gate. A detector that cannot fire is noise and gets deleted; a detector that
reports a number it did not really measure is worse than none, because the
number reads as a measurement and nobody re-derives it.

So the cases below cover the drift it must report AND every way the comparison
can be meaningless -- no deployment at all, a deployment that never succeeded,
a commit this clone does not contain, a history that has diverged. Each of those
must end in a refusal. None of them may end in a comfortable zero.

The sharpest case is `test_a_run_that_published_nothing_is_not_a_deploy`. This
repository's `pages.yml` reaches a runner four times a day and skips every job,
because both nightly publishers sit behind `vars.NIGHTLY_HUB_PUBLISH_ENABLED`
(ADR 0032). A sentinel built on run history would call that a fresh deploy every
six hours while the site stayed two months old. The deployment record is what
makes that impossible, and this test is what keeps it that way.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"


def _script(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before execution, not after: `@dataclass` resolves annotations
    # through `sys.modules[cls.__module__]`, so a module that is not there yet
    # raises on the decorator rather than on anything to do with this repository.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


staleness = _script("deploy_staleness")

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
JULY = "2026-07-12T07:29:54Z"


def _deployment(**over: Any) -> dict[str, Any]:
    row = {
        "id": 5410698107,
        "sha": "c" * 40,
        "environment": "github-pages",
        "created_at": JULY,
    }
    row.update(over)
    return row


def _succeeded(_id: Any) -> list[Mapping[str, Any]]:
    return [{"state": "success"}]


def _never_succeeded(_id: Any) -> list[Mapping[str, Any]]:
    return [{"state": "failure"}, {"state": "in_progress"}]


# --- what the deployment record is allowed to mean --------------------------


def test_the_newest_successful_deployment_is_the_live_build() -> None:
    record = staleness.newest_successful_deployment([_deployment()], _succeeded)
    assert record.sha == "c" * 40
    assert record.created_at.date().isoformat() == "2026-07-12"
    assert record.deployment_id == 5410698107


def test_the_newest_deployment_wins_over_an_older_one() -> None:
    newer = _deployment(id=2, sha="d" * 40, created_at="2026-09-01T00:00:00Z")
    record = staleness.newest_successful_deployment([_deployment(), newer], _succeeded)
    assert record.sha == "d" * 40


def test_a_run_that_published_nothing_is_not_a_deploy() -> None:
    """The trap this file exists for.

    `pages.yml` finishes four times a day having published nothing. Those runs
    create no deployment, so the deployment list is the July one alone and the
    answer stays 63 days -- not "deployed six hours ago". Asserting the absence
    directly, because the bug would be a silent extra row, not an exception.
    """
    skipped_runs_create_no_deployments: list[Mapping[str, Any]] = [_deployment()]
    record = staleness.newest_successful_deployment(skipped_runs_create_no_deployments, _succeeded)
    assert record.created_at.date().isoformat() == "2026-07-12"


def test_no_deployment_at_all_is_a_refusal_not_a_zero() -> None:
    with pytest.raises(staleness.StalenessUnknown, match="no github-pages deployment"):
        staleness.newest_successful_deployment([], _succeeded)


def test_a_deployment_that_never_succeeded_is_a_refusal() -> None:
    with pytest.raises(staleness.StalenessUnknown, match="successful status"):
        staleness.newest_successful_deployment([_deployment()], _never_succeeded)


def test_a_failed_newer_deployment_does_not_hide_the_successful_older_one() -> None:
    """A failed republish leaves the previous build serving; that is the live one."""
    failed = _deployment(id=9, sha="e" * 40, created_at="2026-09-10T00:00:00Z")

    def statuses(deployment_id: Any) -> list[Mapping[str, Any]]:
        return [{"state": "failure"}] if deployment_id == 9 else [{"state": "success"}]

    record = staleness.newest_successful_deployment([_deployment(), failed], statuses)
    assert record.sha == "c" * 40


def test_a_row_without_a_commit_id_is_not_a_deployment() -> None:
    with pytest.raises(staleness.StalenessUnknown, match="no github-pages deployment"):
        staleness.newest_successful_deployment([_deployment(sha="not-a-sha")], _succeeded)


# --- which files change what a visitor receives -----------------------------


@pytest.mark.parametrize(
    "path",
    [
        "scripts/build_evidence_site.py",
        "scripts/site_meta.py",
        ".github/workflows/pages.yml",
        "docs/pages/index.html",
        "docs/pages/feeds/sbmtd.xml",
        "docs/eval-history.svg",
    ],
)
def test_the_publishers_inputs_ship_to_visitors(path: str) -> None:
    assert staleness.ships_to_visitors(path)


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_deploy_staleness.py",
        "docs/adr/0032-nightly-hub.md",
        "src/assistant/guards.py",
        "evals/checks.py",
        "README.md",
    ],
)
def test_everything_else_does_not(path: str) -> None:
    assert not staleness.ships_to_visitors(path)


def test_the_two_publishers_renderers_are_both_on_the_list() -> None:
    """Both publishers, not one. The defect PR #247 fixed was one publisher's

    output being gated while the other's was not; the same asymmetry here would
    be a clock that ignores every change to the nightly renderer, which lives in
    the workflow file rather than in `scripts/`.
    """
    assert staleness.ships_to_visitors("scripts/build_evidence_site.py")
    assert staleness.ships_to_visitors(".github/workflows/pages.yml")


# --- the comparison against main, and every way it can be meaningless -------


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "clone"
    root.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "sentinel@example.test")
    git("config", "user.name", "sentinel")
    return root


def _commit(root: Path, path: str, body: str = "x") -> str:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", path], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", f"touch {path}"],
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _repo(tmp_path)
    monkeypatch.setattr(staleness, "REPO_ROOT", root)
    return root


def _record(sha: str, created_at: datetime) -> Any:
    return staleness.DeployRecord(deployment_id=1, sha=sha, created_at=created_at)


def test_it_counts_the_commits_and_names_the_visitor_visible_ones(clone: Path) -> None:
    deployed = _commit(clone, "README.md")
    _commit(clone, "tests/test_x.py")
    _commit(clone, "scripts/site_meta.py")
    _commit(clone, "docs/pages/index.html")

    drift = staleness.measure(_record(deployed, NOW - timedelta(days=63)), "HEAD", NOW)

    assert drift.commits == 3
    assert drift.visitor_commits == 2
    assert drift.days == 63
    assert drift.overdue


def test_age_alone_is_not_overdue(clone: Path) -> None:
    """A site nobody has republished because nothing it publishes changed is

    correct, not stale. Reporting on age alone would make this sentinel fire on
    every repository that is simply finished, and a sentinel that always fires
    is one nobody reads.
    """
    deployed = _commit(clone, "README.md")
    _commit(clone, "src/assistant/guards.py")

    drift = staleness.measure(_record(deployed, NOW - timedelta(days=200)), "HEAD", NOW)

    assert drift.commits == 1
    assert drift.visitor_commits == 0
    assert not drift.overdue


def test_inside_the_threshold_is_not_overdue(clone: Path) -> None:
    deployed = _commit(clone, "README.md")
    _commit(clone, "scripts/site_meta.py")

    drift = staleness.measure(_record(deployed, NOW - timedelta(days=3)), "HEAD", NOW)

    assert drift.visitor_commits == 1
    assert not drift.overdue


def test_nothing_since_the_deploy_is_up_to_date(clone: Path) -> None:
    deployed = _commit(clone, "docs/pages/index.html")

    drift = staleness.measure(_record(deployed, NOW - timedelta(days=1)), "HEAD", NOW)

    assert drift.commits == 0
    assert drift.visitor_commits == 0
    assert not drift.overdue
    assert "Up to date" in staleness.render(drift)


def test_a_commit_this_clone_does_not_have_is_a_refusal(clone: Path) -> None:
    """The shallow-checkout case, which is the one that reports zero silently.

    `git log <absent>..HEAD` on a shallow clone lists nothing, so the site reads
    as current. This is why the sentinel checks out with `fetch-depth: 0`, and
    why the refusal exists rather than trusting that it did.
    """
    _commit(clone, "README.md")

    with pytest.raises(staleness.StalenessUnknown, match="not in this clone"):
        staleness.measure(_record("a" * 40, NOW - timedelta(days=63)), "HEAD", NOW)


def test_a_diverged_history_is_a_refusal(clone: Path) -> None:
    _commit(clone, "README.md")
    subprocess.run(
        ["git", "-C", str(clone), "checkout", "-b", "other"], check=True, capture_output=True
    )
    orphan = _commit(clone, "orphan.txt")
    subprocess.run(["git", "-C", str(clone), "checkout", "main"], check=True, capture_output=True)

    with pytest.raises(staleness.StalenessUnknown, match="not an ancestor"):
        staleness.measure(_record(orphan, NOW - timedelta(days=63)), "HEAD", NOW)


def test_a_malformed_deployed_sha_is_a_refusal(clone: Path) -> None:
    _commit(clone, "README.md")

    with pytest.raises(staleness.StalenessUnknown, match="not a commit id"):
        staleness.measure(_record("nope", NOW), "HEAD", NOW)


# --- the report, and the exit code --------------------------------------------


def test_the_report_states_the_measurement_before_its_verdict(clone: Path) -> None:
    deployed = _commit(clone, "README.md")
    _commit(clone, "scripts/build_evidence_site.py")
    drift = staleness.measure(_record(deployed, NOW - timedelta(days=63)), "HEAD", NOW)

    report = staleness.render(drift)

    assert deployed[:9] in report
    assert "63 days" in report
    assert "OVERDUE" in report
    assert report.index("Behind by") < report.index("OVERDUE")


def test_the_json_carries_every_number_the_report_states(clone: Path) -> None:
    deployed = _commit(clone, "README.md")
    _commit(clone, "docs/pages/index.html")
    drift = staleness.measure(_record(deployed, NOW - timedelta(days=63)), "HEAD", NOW)

    payload = staleness.as_json(drift)

    assert payload["deployed_sha"] == deployed
    assert payload["days"] == 63
    assert payload["commits"] == 1
    assert payload["visitor_commits"] == 1
    assert payload["overdue"] is True


def test_the_cli_refuses_with_a_nonzero_exit_when_it_cannot_measure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 2, not 0 with a reassuring report. The sentinel workflow turns a

    measurement into an issue and a refusal into a red run, so this exit code is
    the whole difference between "the site is fine" and "nobody can tell".
    """
    payload = tmp_path / "deployments.json"
    payload.write_text('{"deployments": [], "statuses": {}}', encoding="utf-8")

    code = staleness.main(["--deployments-json", str(payload)])

    assert code == 2
    assert "cannot measure" in capsys.readouterr().err
