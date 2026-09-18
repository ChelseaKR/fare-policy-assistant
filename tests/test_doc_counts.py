"""Numbers quoted in the docs must match the repository.

Written after finding `docs/model-card.md` claiming 216 eval cases and
`docs/procurement-brief.md` claiming "118 cases across six suites" when the
repository held 258 across nine. Both were true when written. Neither had any
way to stop being true quietly, and the procurement brief is the document
written for the reader least able to check.

The counts come from the project's own loaders, never from a private reimple-
mentation: `sensitivity.yaml` stores `pairs` of `variants` rather than `cases`,
so a naive YAML count silently reports 228 instead of 258, and a guard that
counts wrongly is worse than no guard at all.
"""

from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path

import yaml

from assistant import config
from evals.runner import load_suites
from scripts import build_evidence_site

DOCS = Path(__file__).resolve().parents[1] / "docs"


def _case_count() -> int:
    return sum(len(suite.get("cases") or []) for suite in load_suites())


def _suite_count() -> int:
    return len(load_suites())


def _agency_count() -> int:
    manifest = yaml.safe_load(config.MANIFEST_PATH.read_text(encoding="utf-8"))
    return len({doc["agency"] for doc in manifest["documents"]})


def _numbers_before(text: str, noun: str) -> set[str]:
    """Every number written immediately before `noun` in this document."""
    return set(re.findall(rf"(\d+)\s+{noun}", text))


def test_model_card_case_count_matches_the_suites() -> None:
    text = (DOCS / "model-card.md").read_text(encoding="utf-8")
    claimed = _numbers_before(text, "cases")
    assert claimed, "model-card.md no longer states a case count; update this guard too"
    assert claimed == {str(_case_count())}, (
        f"docs/model-card.md claims {sorted(claimed)} cases; the suites hold {_case_count()}"
    )


def test_procurement_brief_case_count_matches_the_suites() -> None:
    text = (DOCS / "procurement-brief.md").read_text(encoding="utf-8")
    claimed = _numbers_before(text, "cases")
    assert claimed, "procurement-brief.md no longer states a case count; update this guard too"
    assert claimed == {str(_case_count())}, (
        f"docs/procurement-brief.md claims {sorted(claimed)} cases; the suites hold {_case_count()}"
    )


def test_procurement_brief_suite_count_matches_the_suites() -> None:
    text = (DOCS / "procurement-brief.md").read_text(encoding="utf-8")
    words = {
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
    }
    claimed = {
        words[word] for word in re.findall(r"([a-z]+) suites", text.lower()) if word in words
    }
    claimed |= {int(n) for n in re.findall(r"(\d+) suites", text)}
    assert claimed == {_suite_count()}, (
        f"docs/procurement-brief.md claims {sorted(claimed)} suites; there are {_suite_count()}"
    )


def test_docs_do_not_understate_the_agency_count() -> None:
    """Agency counts in prose must match the manifest.

    Four agencies were added on 2026-08-12/13 and several documents kept saying
    five, six or seven, each correct at the moment it was written.
    """
    words = {
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
    }
    actual = _agency_count()
    wrong: list[str] = []
    for name in ("model-card.md", "procurement-brief.md"):
        text = (DOCS / name).read_text(encoding="utf-8")
        for word in re.findall(r"([a-z]+) agencies", text.lower()):
            if word in words and words[word] != actual:
                wrong.append(f"{name} says '{word} agencies'")
        for digits in re.findall(r"(\d+) agencies", text):
            if int(digits) != actual:
                wrong.append(f"{name} says '{digits} agencies'")
    assert not wrong, f"{wrong}; the manifest holds {actual}"


# ── the release pipeline the README describes ───────────────────────────────
#
# Added after finding the README's Release & Versioning conformance row saying
# `release.yml` "is tag-triggered on `v*`" for the 45 days after bd083d5
# (2026-07-23) replaced that trigger with `workflow_dispatch`. A reader
# following the README would push a signed tag and watch nothing happen. The
# repository has no tags and no releases, so the documented path was the only
# evidence anyone had about how a release is cut, and it was wrong.

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _release_triggers() -> dict:
    """The `on:` block of the release workflow, as data.

    PyYAML resolves the bare key `on` to the boolean `True` (YAML 1.1), which
    is why this looks the key up both ways rather than as the string alone. A
    lookup that quietly found nothing would make every assertion below vacuous.
    """
    data = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    for key in ("on", True):
        if key in data:
            return data[key] or {}
    raise AssertionError(f"{RELEASE_WORKFLOW.name} has no `on:` block")


def test_readme_describes_the_release_workflows_actual_trigger():
    triggers = _release_triggers()
    push_tags = (triggers.get("push") or {}).get("tags")
    readme = README.read_text(encoding="utf-8")

    if push_tags:
        # The affirmative phrasing, not merely the words. This README now
        # contains "was tag-triggered ... until", which is a history note; a
        # substring check would accept it as a live claim and let a restored
        # tag trigger go undocumented.
        assert "is tag-triggered on" in readme, (
            "release.yml fires on a tag push and the README does not say so in the present tense"
        )
    else:
        assert "is tag-triggered on" not in readme, (
            "the README calls release.yml tag-triggered, but its `on:` block has "
            f"no push.tags — it is {sorted(triggers)}. A reader following this "
            "pushes a tag and nothing runs."
        )
        assert "workflow_dispatch" in readme, (
            "release.yml is dispatch-only; the README must say how it is actually "
            "invoked, or the pipeline is undocumented"
        )
    assert "workflow_dispatch" in triggers or push_tags, "release.yml must be reachable somehow"


def test_the_version_the_release_pipeline_would_check_is_consistent():
    """`pyproject`, `CITATION.cff` and `CHANGELOG.md` have to agree.

    The workflow's build job requires the tag to equal `pyproject.toml`'s
    version and then extracts release notes by matching a `## [<version>]`
    heading in `CHANGELOG.md`, failing on an empty extract. If those three
    drift, the first release anyone attempts dies at that step — and since
    this repository has never cut one, nothing else would have caught it.
    """
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]

    citation = yaml.safe_load((REPO_ROOT / "CITATION.cff").read_text(encoding="utf-8"))
    assert str(citation["version"]) == version, (
        f"CITATION.cff declares {citation['version']!r}, pyproject {version!r}"
    )

    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{version}]" in changelog, (
        f"CHANGELOG.md has no '## [{version}]' section, so the release "
        "workflow's notes extraction would produce an empty file and fail"
    )


# ── the evidence-hub publication runbook ────────────────────────────────────
#
# Added after finding that `docs/publishing-the-evidence-hub.md`'s publication
# command — the only runbook anyone has for the manual path to
# https://evals.chelseakr.com/ — read its three promotion receipts out of
# `<run_dir>`, which reads as one of the `evals/runs/` directories `make eval`
# writes. No run directory has ever held a `promotion.json`, and none can:
# `infra/deploy.sh` stages all three receipts under `$BUILD/promotion` and is
# the only thing in the repository that composes the third. An operator
# following that text went looking in a directory that does not produce the
# file. The prose around the command can only be reviewed by reading it; the
# command itself is copied and run, so it is checked here instead.

PUBLISHING_DOC = DOCS / "publishing-the-evidence-hub.md"
PAGES_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pages.yml"
DEPLOY_SCRIPT = REPO_ROOT / "infra" / "deploy.sh"


def _publishing_runbook() -> str:
    """The one fenced shell block the publishing doc tells an operator to run."""
    blocks = re.findall(r"```sh\n(.*?)```", PUBLISHING_DOC.read_text(encoding="utf-8"), re.S)
    assert len(blocks) == 1, (
        f"{PUBLISHING_DOC.name} holds {len(blocks)} ```sh blocks; these guards read the "
        "publication command and cannot tell which one that is any more"
    )
    # Join backslash continuations so one shell command is one line to scan.
    return blocks[0].replace("\\\n", " ")


def _runbook_command(needle: str) -> str:
    lines = [line for line in _publishing_runbook().splitlines() if needle in line]
    assert len(lines) == 1, (
        f"expected exactly one {needle!r} command in {PUBLISHING_DOC.name}, found {len(lines)}"
    )
    return lines[0]


def _export_subparser() -> argparse.ArgumentParser:
    (subparsers,) = [
        action
        for action in build_evidence_site._parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    return subparsers.choices["export"]


def test_documented_dispatch_passes_exactly_the_workflows_declared_inputs() -> None:
    """`gh workflow run pages.yml` must name every input, and no other.

    A missing `-f` fails the dispatch at the workflow's own shape check with no
    hint about which key; an extra one is silently dropped, which is worse,
    because the operator believes they pinned something they did not.
    """
    documented = set(re.findall(r"-f (\w+)=", _publishing_runbook()))
    parsed = yaml.load(PAGES_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    declared = set(parsed["on"]["workflow_dispatch"]["inputs"])

    assert documented, f"{PUBLISHING_DOC.name} no longer dispatches pages.yml; update this guard"
    assert documented == declared, (
        f"{PUBLISHING_DOC.name} dispatches with {sorted(documented)}; "
        f"pages.yml declares {sorted(declared)}"
    )


def test_documented_export_flags_are_flags_the_exporter_accepts() -> None:
    command = _runbook_command("build_evidence_site.py export")
    accepted = {
        option for action in _export_subparser()._actions for option in action.option_strings
    }
    documented = set(re.findall(r"(--[a-z0-9-]+)", command))

    assert documented, "the documented export command passes no flags at all"
    assert documented <= accepted, (
        f"{PUBLISHING_DOC.name} passes {sorted(documented - accepted)} to "
        "`build_evidence_site.py export`, which its parser does not define"
    )


def test_documented_export_passes_every_flag_the_exporter_requires() -> None:
    """The reverse drift: a new required flag the runbook never learned about.

    Every argument of the `export` subcommand is `required=True` today, so a
    runbook missing one produces an argparse error rather than a manifest, and
    the operator finds out after the deploy that paid for the receipts.
    """
    command = _runbook_command("build_evidence_site.py export")
    required = {
        option
        for action in _export_subparser()._actions
        if action.required
        for option in action.option_strings
    }

    assert required, "the export subcommand requires nothing; this guard has gone vacuous"
    missing = {flag for flag in required if flag not in command}
    assert not missing, (
        f"`build_evidence_site.py export` requires {sorted(missing)}, which "
        f"{PUBLISHING_DOC.name}'s command does not pass"
    )


def test_the_runbook_reads_the_receipts_the_deploy_actually_writes() -> None:
    """Step 1's inputs must be the paths `infra/deploy.sh` stages, not a run dir.

    Both halves are read out of `deploy.sh` rather than written down here, so
    moving the staging directory fails this test instead of silently making the
    documented path wrong for a second time.
    """
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    build = re.search(r'^BUILD="\$\{FPA_BUILD_DIR:-\$ROOT/([^}"]+)\}"', deploy, re.M)
    promotion = re.search(r'^PROMOTION_BUILD="\$BUILD/([^"]+)"', deploy, re.M)
    assert build and promotion, (
        "infra/deploy.sh no longer defines BUILD and PROMOTION_BUILD the way this guard "
        "reads them; the documented receipt paths can no longer be checked"
    )
    staged = f"{build.group(1)}/{promotion.group(1)}"
    command = _runbook_command("build_evidence_site.py export")

    for receipt in ("summary.json", "results.jsonl", "promotion.json"):
        assert f"{staged}/{receipt}" in command, (
            f"{PUBLISHING_DOC.name} does not export from {staged}/{receipt}, which is "
            "where infra/deploy.sh stages it and the only place it is produced"
        )
