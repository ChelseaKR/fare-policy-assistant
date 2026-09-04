"""Tests for the nightly evidence-hub publication path added by ADR 0032.

`pages.yml`'s `workflow_dispatch` promotion pipeline already has its own
coverage in `test_build_evidence_site.py`. This file covers the second path:
the `workflow_run` job pair that republishes the nightly's own EVALS.md /
docs/eval-report.html automatically, pass or fail, behind an off-by-default
kill switch (issue #140).

Two classes of test, on purpose. Structural checks alone would pass against a
workflow whose render step never actually runs -- the exact failure mode ADR
0030 names for the promotion pipeline's own freshness script -- so this file
also extracts the render step's embedded Python and the published page's
inline freshness script and executes both, the way the workflow and a
browser actually would.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from bs4 import BeautifulSoup

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "pages.yml"
_NIGHTLY_TEMPLATE = _REPO_ROOT / "docs" / "pages" / "nightly-index.html"
_NODE = shutil.which("node")

#: The exact set the render step (extracted below) knows how to fill. Kept as
#: an explicit list here, rather than derived from the template, so a marker
#: added to one side without the other fails a test instead of only failing at
#: publish time.
_EXPECTED_PLACEHOLDERS = {
    "SCRIPT_SRC_HASH",
    "RUN_DATE",
    "TOTAL_SCORE",
    "GATE_WORD",
    "GATE_CLASS",
    "GATE_LABEL",
    "GATE_DETAIL",
    "RUN_AT",
    "RUN_MODE",
    "CORPUS_VERSION",
    "RUN_URL",
    "PUBLISHED_AT",
    "EXPIRES_AT",
    "SUITE_ROWS",
    "FRESHNESS_SCRIPT",
}


def _workflow() -> tuple[str, dict]:
    text = _WORKFLOW.read_text(encoding="utf-8")
    parsed = yaml.load(text, Loader=yaml.BaseLoader)
    assert isinstance(parsed, dict)
    return text, parsed


def test_the_workflow_does_not_use_workflow_run() -> None:
    """Regression test for the design this file replaced.

    An earlier version of this workflow used `workflow_run` on CI's
    completion to drive the nightly path. zizmor's `dangerous-triggers` rule
    flags that trigger categorically (`workflow_run is almost always used
    insecurely`), and this repo's zizmor gate is unsuppressible by design
    (`zizmor (blocking; no mute)`) -- 26 other findings are muted inline and
    this rule is not, on purpose. `test_zizmor_reports_no_findings` below
    proves the live consequence when zizmor is available locally; this
    assertion is the one-line version that always runs, so a `workflow_run`
    reintroduced here fails fast without needing zizmor installed at all.
    """
    _, parsed = _workflow()
    assert "workflow_run" not in parsed["on"]
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert not re.search(r"^\s*workflow_run:", text, flags=re.MULTILINE), (
        "a workflow_run trigger key reappeared -- comments discussing the "
        "trigger by name are fine, a `workflow_run:` block under `on:` is not"
    )


def test_zizmor_reports_no_findings() -> None:
    """The exact command CI's unsuppressible `zizmor (blocking; no mute)` step

    runs. Skipped, not failed, when zizmor is not on PATH: unlike the
    freshness-script check below (which has no other executor anywhere in
    CI), this property already has a real, always-on, unsuppressible gate in
    `.github/workflows/zizmor.yml` on every PR that touches workflow files --
    a local skip here does not create a check that cannot fail, it just means
    this particular machine cannot preview that gate's answer early.
    """
    zizmor = shutil.which("zizmor")
    if zizmor is None:
        pytest.skip("zizmor is not on PATH; the real gate is .github/workflows/zizmor.yml")
    completed = subprocess.run(
        [zizmor, "--min-severity", "high", str(_WORKFLOW.parent)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_schedule_trigger_polls_several_times_a_day() -> None:
    """A single check a day would race a slow nightly and, worse, would only

    notice a missing nightly once every 24 hours instead of within one
    six-hour cycle.
    """
    _, parsed = _workflow()
    crons = parsed["on"]["schedule"]
    assert len(crons) == 1
    hours = crons[0]["cron"].split()[1]
    assert len(hours.split(",")) >= 4, f"expected at least four checks a day, got {hours!r}"


def test_nightly_jobs_publish_a_run_regardless_of_its_conclusion() -> None:
    """The regression this test exists to catch: gating publication on the

    nightly *passing* reproduces issue #140 exactly, just with a later frozen
    date, because the nightly has failed fourteen nights running when this
    was written. The job-level `if:` no longer mentions success/failure at
    all (that decision moved into the "Find the most recently completed
    nightly CI run" step, which is why it is tested by executing the render
    step against both conclusions below) -- what the job-level guard must
    still never do is require a specific conclusion.
    """
    _, parsed = _workflow()
    for job in ("nightly-build", "nightly-deploy"):
        condition = parsed["jobs"][job]["if"]
        assert "conclusion == 'success'" not in condition, (
            f"{job} must not gate on the nightly passing"
        )
        assert "workflow_run" not in condition


def test_nightly_publication_is_off_by_default_behind_a_named_switch() -> None:
    """Landing this workflow must not, by itself, publish anything.

    The first publish through this path is an explicit operator action (set
    `vars.NIGHTLY_HUB_PUBLISH_ENABLED`), not a consequence of merging a PR or
    of a nightly run completing. See ADR 0032.
    """
    _, parsed = _workflow()
    for job in ("nightly-build", "nightly-deploy"):
        condition = parsed["jobs"][job]["if"]
        assert "vars.NIGHTLY_HUB_PUBLISH_ENABLED == 'true'" in condition, (
            f"{job} is missing its off-by-default publication switch"
        )


def test_manual_dispatch_jobs_are_scoped_away_from_schedule_events() -> None:
    """Adding a second trigger to `on:` without gating the original jobs would

    make `build`/`deploy` attempt to run (and fail on empty `inputs.*`) on
    every scheduled nightly-poll tick too.
    """
    _, parsed = _workflow()
    for job in ("build", "deploy"):
        assert parsed["jobs"][job]["if"] == "github.event_name == 'workflow_dispatch'"


def test_nightly_jobs_never_use_a_write_credential_to_reach_the_repository() -> None:
    """No evidence-only commit exists in this path; nothing here should be

    minting a token to write one. `contents: read` only, plus `actions: read`
    to fetch the nightly's own artifact and the Pages-deploy permissions the
    manual pipeline already uses.
    """
    _, parsed = _workflow()
    build_perms = parsed["jobs"]["nightly-build"]["permissions"]
    assert build_perms.get("contents") == "read"
    assert "write" not in build_perms.values() or build_perms == {
        "contents": "read",
        "actions": "read",
    }


def test_nightly_template_placeholders_match_what_the_render_step_fills() -> None:
    template = _NIGHTLY_TEMPLATE.read_text(encoding="utf-8")
    found = set(re.findall(r"\{\{([A-Z][A-Z0-9_]*)\}\}", template))
    assert found == _EXPECTED_PLACEHOLDERS


def test_nightly_template_csp_has_no_unsafe_inline() -> None:
    template = _NIGHTLY_TEMPLATE.read_text(encoding="utf-8")
    assert "unsafe-inline" not in re.search(r"script-src[^;]*", template).group(0)
    assert "default-src 'none'" in template


def _extract_render_step() -> str:
    """The exact `run:` script the nightly-build job executes, as text.

    Reads it out of the workflow rather than duplicating it, so a change to
    the real step is what this test exercises -- the same reasoning ADR 0030
    gives for running the published freshness script instead of asserting
    about its source text.
    """
    _, parsed = _workflow()
    steps = parsed["jobs"]["nightly-build"]["steps"]
    (step,) = [s for s in steps if s.get("name", "").startswith("Render the nightly snapshot")]
    return step["run"]


def _run_render_step(
    tmp_path: Path,
    *,
    conclusion: str,
    evals_md: str,
    run_url: str = "https://github.com/ChelseaKR/fare-policy-assistant/actions/runs/1",
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "nightly-report" / "docs").mkdir(parents=True)
    (tmp_path / "nightly-report" / "EVALS.md").write_text(evals_md, encoding="utf-8")
    (tmp_path / "nightly-report" / "docs" / "eval-report.html").write_text(
        "<html><body>sanitized report</body></html>", encoding="utf-8"
    )
    (tmp_path / "source" / "docs" / "pages").mkdir(parents=True)
    shutil.copy(_NIGHTLY_TEMPLATE, tmp_path / "source" / "docs" / "pages" / "nightly-index.html")
    (tmp_path / "source" / "docs" / "pages" / "CNAME").write_text(
        "evals.chelseakr.com\n", encoding="ascii"
    )
    script = tmp_path / "render.sh"
    script.write_text(_extract_render_step(), encoding="utf-8")
    env = dict(os.environ, RUN_CONCLUSION=conclusion, RUN_URL=run_url)
    return subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )


def _fixture_evals_md(*, run_at: str = "2026-09-03T09:17:02+00:00") -> str:
    provenance = {
        "corpus_version": "3dd8b7bd757e",
        "parity": {},
        "prompt_versions": {},
        "run_id": run_at,
        "suites": {
            "conversation": {"pass_rate": 70.0, "passed": 7, "total": 10},
            "cross_agency": {"pass_rate": 19.0, "passed": 4, "total": 21},
        },
    }
    return (
        "# Evaluation Report\n\n"
        f"Generated from the run at `{run_at}` (full, live provider — every "
        "model call served from cache).\n\n"
        f"<!-- provenance {json.dumps(provenance)} -->\n"
    )


def test_render_step_publishes_a_failing_run_labeled_as_failing(tmp_path: Path) -> None:
    completed = _run_render_step(tmp_path, conclusion="failure", evals_md=_fixture_evals_md())
    assert completed.returncode == 0, completed.stderr

    page = (tmp_path / "_site" / "index.html").read_text(encoding="utf-8")
    assert "{{" not in page, "an unfilled placeholder reached the published page"
    status_label = BeautifulSoup(page, "html.parser").find(id="evidence-status-label")
    assert status_label.get_text(strip=True) == "Nightly gate: FAILED", (
        "the visible status label must never say 'Verified' -- that is the "
        "promotion pipeline's word, for a run whose gates did not pass"
    )
    assert "Nightly gate: FAILED" in page
    assert "11/31" in page  # 7+4 passed of 10+21 total, from the fixture suites
    assert '<section class="notice warn"' in page

    report = (tmp_path / "_site" / "report.html").read_text(encoding="utf-8")
    assert "sanitized report" in report


def test_render_step_publishes_a_passing_run_labeled_as_passing(tmp_path: Path) -> None:
    completed = _run_render_step(tmp_path, conclusion="success", evals_md=_fixture_evals_md())
    assert completed.returncode == 0, completed.stderr
    page = (tmp_path / "_site" / "index.html").read_text(encoding="utf-8")
    assert "Nightly gate: passed" in page
    assert '<section class="notice ok"' in page


def test_render_step_refuses_to_publish_without_a_provenance_block(tmp_path: Path) -> None:
    broken = "# Evaluation Report\n\nNo provenance block here.\n"
    completed = _run_render_step(tmp_path, conclusion="failure", evals_md=broken)
    assert completed.returncode != 0
    assert not (tmp_path / "_site").exists()


def test_render_step_writes_robots_and_sitemap_naming_the_real_origin(tmp_path: Path) -> None:
    completed = _run_render_step(tmp_path, conclusion="success", evals_md=_fixture_evals_md())
    assert completed.returncode == 0, completed.stderr
    robots = (tmp_path / "_site" / "robots.txt").read_text(encoding="utf-8")
    assert "Sitemap: https://evals.chelseakr.com/sitemap.xml" in robots
    sitemap = (tmp_path / "_site" / "sitemap.xml").read_text(encoding="utf-8")
    assert "https://evals.chelseakr.com/" in sitemap
    assert "https://evals.chelseakr.com/report.html" in sitemap


# --- the published page's own read-time freshness check --------------------

_FRESHNESS_DRIVER = """\
"use strict";
const fs = require("fs");
const [scriptPath, statePath] = process.argv.slice(2);
const state = JSON.parse(fs.readFileSync(statePath, "utf8"));
const nodes = {};
for (const [id, node] of Object.entries(state.nodes)) {
  nodes[id] = {
    className: node.className,
    textContent: node.textContent,
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(node.attributes, name)
        ? node.attributes[name]
        : null;
    },
  };
}
globalThis.document = { getElementById: (id) => nodes[id] || null };
const reading = Date.parse(state.now);
if (!isFinite(reading)) { throw new Error("the test clock is not a parsable instant"); }
Date.now = () => reading;
new Function(fs.readFileSync(scriptPath, "utf8"))();
const observed = {};
for (const [id, node] of Object.entries(nodes)) {
  observed[id] = { className: node.className, textContent: node.textContent };
}
process.stdout.write(JSON.stringify(observed));
"""


def _read_nightly_page_as_of(page: str, now: str, workdir: Path) -> dict[str, dict[str, str]]:
    if _NODE is None:
        pytest.fail(
            "node is not on PATH, so the nightly page's read-time freshness check "
            "cannot be executed here. Skipping would report the stale state as "
            "proven while nothing had run it."
        )
    soup = BeautifulSoup(page, "html.parser")
    nodes: dict[str, object] = {}
    for identifier in (
        "evidence-status",
        "evidence-status-label",
        "evidence-status-detail",
        "evidence-freshness",
    ):
        element = soup.find(id=identifier)
        assert element is not None, f"the nightly page carries no #{identifier}"
        nodes[identifier] = {
            "className": " ".join(element.get("class", [])),
            "textContent": element.get_text(" ", strip=True),
            "attributes": {
                name: value for name, value in element.attrs.items() if name.startswith("data-")
            },
        }
    workdir.mkdir(parents=True, exist_ok=True)
    driver = workdir / "driver.js"
    driver.write_text(_FRESHNESS_DRIVER, encoding="utf-8")
    script_element = soup.find("script")
    assert script_element is not None
    script = workdir / "published.js"
    script.write_text(str(script_element.string), encoding="utf-8")
    state = workdir / "state.json"
    state.write_text(json.dumps({"now": now, "nodes": nodes}), encoding="utf-8")
    completed = subprocess.run(
        [_NODE, str(driver), str(script), str(state)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def test_the_nightly_page_reports_stale_once_it_outlives_two_missed_nights(
    tmp_path: Path,
) -> None:
    run_at = "2026-09-03T09:17:02+00:00"
    completed = _run_render_step(
        tmp_path, conclusion="failure", evals_md=_fixture_evals_md(run_at=run_at)
    )
    assert completed.returncode == 0, completed.stderr
    page = (tmp_path / "_site" / "index.html").read_text(encoding="utf-8")

    fresh = _read_nightly_page_as_of(page, "2026-09-03T20:00:00Z", tmp_path / "fresh")
    assert fresh["evidence-status"]["className"] == "notice warn"

    stale = _read_nightly_page_as_of(page, "2026-09-07T09:00:00Z", tmp_path / "stale")
    assert stale["evidence-status"]["className"] == "notice stale"
    assert "STALE" in stale["evidence-status-label"]["textContent"]
    assert "nightly workflow has not published" in stale["evidence-status-detail"]["textContent"]


def test_the_nightly_page_csp_hash_matches_the_script_it_actually_inlines(
    tmp_path: Path,
) -> None:
    """The same drift class ADR 0030 guards against on the promotion page:

    a CSP that admits a digest computed from something other than the bytes
    on the page would silently stop the check from ever executing in a real
    browser while every other test still passed.
    """
    import base64
    import hashlib

    completed = _run_render_step(tmp_path, conclusion="failure", evals_md=_fixture_evals_md())
    assert completed.returncode == 0, completed.stderr
    page = (tmp_path / "_site" / "index.html").read_text(encoding="utf-8")
    script = BeautifulSoup(page, "html.parser").find("script").string
    digest = "sha256-" + base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode(
        "ascii"
    )
    assert f"script-src '{digest}'" in page
