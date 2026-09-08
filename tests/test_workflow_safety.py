"""Static release-workflow invariants that must hold even when a job fails."""

import re
import subprocess

import pytest
import yaml

from assistant import config


def _ci_workflow() -> tuple[str, dict]:
    text = (config.REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    parsed = yaml.load(text, Loader=yaml.BaseLoader)
    assert isinstance(parsed, dict)
    return text, parsed


def _job(name: str, next_name: str) -> str:
    text, _ = _ci_workflow()
    return text.split(f"  {name}:", 1)[1].split(f"  {next_name}:", 1)[0]


def test_failed_full_eval_still_uploads_evidence():
    full_eval = _job("full-evals-nightly", "independent-audit")
    upload = full_eval.split("- name: Upload report", 1)[1]
    assert "if: always()" in upload, (
        "nightly evaluation failures must retain their partial report, traces, "
        "and provenance for diagnosis"
    )


def test_pull_request_eval_is_explicitly_offline_and_cannot_mint_oidc_tokens():
    _, workflow = _ci_workflow()
    jobs = workflow["jobs"]
    offline = jobs["smoke-evals"]

    assert offline["if"] == "github.event_name == 'pull_request'"
    assert offline["permissions"] == {"contents": "read"}
    commands = [step.get("run", "") for step in offline["steps"]]
    assert any("make eval-selftest" in command for command in commands)
    assert all("evals.runner" not in command for command in commands)
    assert all(
        "configure-aws-credentials" not in str(step.get("uses", "")) for step in offline["steps"]
    )


def test_only_non_pr_eval_jobs_receive_oidc_permission():
    _, workflow = _ci_workflow()
    jobs = workflow["jobs"]
    online = jobs["smoke-evals-online"]
    nightly = jobs["full-evals-nightly"]

    assert online["if"] == "github.event_name == 'push'"
    assert online["permissions"]["id-token"] == "write"
    assert nightly["if"] == "github.event_name == 'schedule'"
    assert nightly["permissions"]["id-token"] == "write"
    oidc_jobs = {
        name
        for name, job in jobs.items()
        if isinstance(job, dict)
        and isinstance(job.get("permissions"), dict)
        and job["permissions"].get("id-token") == "write"
    }
    assert oidc_jobs == {"smoke-evals-online", "full-evals-nightly"}


@pytest.mark.parametrize(
    ("job", "next_job"),
    [("smoke-evals-online", "full-evals-nightly"), ("full-evals-nightly", "independent-audit")],
)
def test_paid_eval_jobs_restore_and_save_the_model_cache(job, next_job):
    """ADR 0022. Model calls are this project's largest AWS line. A job that
    starts cold re-buys answers it already has, so both paid eval jobs must
    restore the content-keyed cache and save it again afterwards.

    ``smoke-evals`` itself is offline (self-test only, no model calls, see
    ``test_pull_request_eval_is_explicitly_offline_and_cannot_mint_oidc_tokens``)
    so it has nothing to cache; ``smoke-evals-online`` is the paid job that
    replaced it post-merge."""
    body = _job(job, next_job)
    assert "actions/cache/restore@" in body, f"{job} must restore evals/cache before running"
    assert "actions/cache/save@" in body, f"{job} must save evals/cache after running"
    assert "restore-keys: eval-model-cache-" in body, (
        f"{job}'s restore must fall through to the shared prefix, so it can inherit the "
        "cache the nightly full run wrote on the default branch"
    )


@pytest.mark.parametrize(
    ("job", "next_job"),
    [("smoke-evals-online", "full-evals-nightly"), ("full-evals-nightly", "independent-audit")],
)
def test_a_failing_eval_still_saves_the_calls_it_paid_for(job, next_job):
    """A red regression gate does not make the model calls free. Saving only on
    success would make a week of failing nightlies re-buy the same 201 cases
    every morning."""
    save = _job(job, next_job).split("- name: Save the answer/judge model cache", 1)[1]
    assert "if: always()" in save.split("uses:", 1)[0], (
        f"{job} must save the model cache even when the run fails its gate"
    )


def test_one_scheduled_full_run_a_week_is_cold():
    """ADR 0022 trades six nights of provider-drift detection for cost, and buys
    it back with one cold run a week. If every nightly were cache-served,
    nothing would ever re-measure the provider."""
    workflow, _ = _ci_workflow()
    crons = [line for line in workflow.splitlines() if line.strip().startswith("- cron:")]
    assert len(crons) >= 2, "expected a cached nightly cron and a separate cold weekly cron"
    full_eval = _job("full-evals-nightly", "independent-audit")
    assert "--refresh-cache" in full_eval, (
        "the weekly schedule must run the full suite with --refresh-cache, so it both "
        "re-measures the provider and leaves the cache agreeing with what it published"
    )
    cold_cron = full_eval.split("github.event.schedule == '", 1)[1].split("'", 1)[0]
    assert any(cold_cron in line for line in crons), (
        f"the cold-run schedule guard names {cold_cron!r}, which is not one of this "
        "workflow's cron entries — a renamed cron would silently make every nightly cached"
    )


def test_paid_eval_jobs_are_never_promotion_evidence():
    """ADR 0023 (evaluation identity and promotion attestation): a promotable
    run must be uncached, and cache speedups 'remain available for
    development, but cannot enter promotion evidence'. The actual promotion
    run is its own full, live, --no-cache invocation at deploy time
    (infra/deploy.sh), independent of these CI jobs — so it is correct, not a
    regression, for smoke-evals-online and full-evals-nightly to be
    cache-backed here."""
    deploy_script = (config.REPO_ROOT / "infra" / "deploy.sh").read_text(encoding="utf-8")
    assert "--full" in deploy_script and "--no-cache" in deploy_script, (
        "promotion must still run its own full, uncached evaluation independent of "
        "the routine CI jobs' caching"
    )


def test_ci_checks_job_calls_the_makefile_gate_targets_rather_than_respelling_them():
    """The `checks` job and `make verify` must be one definition, not two copies.

    They were two copies until 2026-08-28, and they had drifted: the Makefile
    linted `src tests evals web scripts` and typechecked `src web scripts` while
    this job omitted `scripts` from both. `C90` (CQ-05) had therefore never been
    able to report a finding in the evidence-site or promotion-attestation
    builders, four C901 violations sat there for a week, `make verify` was red on
    `main`, and every CI run over that same tree was green.

    Re-spelling a command line here is what allowed that, so the gate is: every
    step in this job that has a local Makefile equivalent must invoke it.
    """
    _, workflow = _ci_workflow()
    steps = workflow["jobs"]["checks"]["steps"]
    commands = [step.get("run", "").strip() for step in steps if "run" in step]

    for target in ("lint", "typecheck", "test", "a11y", "report-regression", "provenance"):
        assert f"make {target}" in commands, (
            f"the checks job no longer calls `make {target}`. Re-spelling the command "
            "here is how CI and the Makefile drifted before; call the target instead."
        )
    respelled = [
        command
        for command in commands
        if command.startswith("uv run ") and ("ruff" in command or "mypy" in command)
    ]
    assert not respelled, (
        f"these steps bypass the Makefile's LINT_PATHS/TYPE_PATHS: {respelled}. "
        "A hand-copied path list is the exact drift this job is guarded against."
    )


def _corpus_freshness_workflow() -> dict:
    text = (config.REPO_ROOT / ".github" / "workflows" / "corpus-freshness.yml").read_text(
        encoding="utf-8"
    )
    parsed = yaml.load(text, Loader=yaml.BaseLoader)
    assert isinstance(parsed, dict)
    return parsed


def _refresh_pr_add_paths() -> list[str]:
    """The pathspecs the corpus-refresh PR step hands to `git add`.

    `peter-evans/create-pull-request` splits `add-paths` on newlines and passes
    each line to `git add --` as one argument, with no shell in between.
    """
    steps = _corpus_freshness_workflow()["jobs"]["refresh"]["steps"]
    pr_step = next(step for step in steps if str(step.get("uses", "")).startswith("peter-evans/"))
    return [line for line in pr_step["with"]["add-paths"].splitlines() if line.strip()]


def test_the_corpus_refresh_pathspecs_are_ones_git_actually_accepts(tmp_path):
    """The refresh PR step's `add-paths` must survive a real `git add`.

    Read as a string this list looked right for eight weeks. It was not: the
    exclude was written as ``':!corpus/raw/fetch-failures.json'`` and the quotes
    went to git as part of the pathspec, so git answered ``fatal: pathspec
    '':!corpus/raw/fetch-failures.json'' did not match any files``, staged
    nothing, and the action aborted with an empty "Unexpected error". Every run
    of the workflow from 2026-07-13 to 2026-09-07 failed there, and no corpus
    refresh PR has ever opened.

    A string assertion would only have caught the shape someone thought to
    forbid, so this runs the pathspecs against git instead. `git add` is the
    thing that was wrong; `git add` is the thing that gets tested.
    """
    repo = tmp_path / "repo"
    (repo / "corpus" / "raw").mkdir(parents=True)
    (repo / "corpus" / "manifest.yaml").write_text("documents: []\n", encoding="utf-8")
    (repo / "corpus" / "raw" / "kept.html").write_text("<p>kept</p>\n", encoding="utf-8")
    (repo / "corpus" / "raw" / "fetch-failures.json").write_text(
        '{"failed": []}\n', encoding="utf-8"
    )
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)

    add = subprocess.run(
        ["git", "add", "--", *_refresh_pr_add_paths()],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert add.returncode == 0, (
        "git rejected the workflow's add-paths, so the refresh PR step stages "
        f"nothing and the action aborts: {add.stderr.strip()}"
    )

    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert "corpus/manifest.yaml" in staged, "the refresh must stage the corpus it just rebuilt"
    assert "corpus/raw/kept.html" in staged
    assert "corpus/raw/fetch-failures.json" not in staged, (
        "the unreachable-sources report is a run artifact, not corpus content"
    )


# ── reusable-workflow calls ──────────────────────────────────────────────────

# A **public** repository cannot call a reusable workflow that lives in a
# **private** one, whatever the private repository's Actions access level says.
# GitHub reports the refusal as
#
#     failed to parse workflow: error parsing called workflow
#     "OWNER/REPO/.github/workflows/x.yml@<sha>": workflow was not found
#
# which reads as a deleted file, so the real cause is easy to miss. It is the
# reason `release.yml` had never executed once: it called
# `ChelseaKR/portfolio-standards`, which is private, and the dispatch died before
# any job started.
#
# Rather than pin one SHA (which would go stale on every legitimate bump), this
# pins the *repositories* a reusable workflow may be called from. Add one here
# only after confirming it is public.
PUBLIC_REUSABLE_WORKFLOW_REPOS = frozenset({"ChelseaKR/.github"})

# Known-private, and named so the failure message can say why rather than only
# that the repository is not on the list.
PRIVATE_REPOS = frozenset({"ChelseaKR/portfolio-standards"})


def _reusable_workflow_calls() -> list[tuple[str, str, str, str]]:
    """Every cross-repository reusable-workflow call, as (file, job, repo, ref).

    A reusable-workflow `uses:` is a job-level key whose value is
    `OWNER/REPO/.github/workflows/<file>@<ref>`; a step-level `uses:` names an
    *action* and is a different thing with different rules, so only job values
    are read. A `./`-prefixed local call has no repository and is skipped.
    """
    calls: list[tuple[str, str, str, str]] = []
    for path in sorted((config.REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        parsed = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        for job_name, job in (parsed.get("jobs") or {}).items():
            uses = job.get("uses") if isinstance(job, dict) else None
            if not isinstance(uses, str) or uses.startswith("./"):
                continue
            target, _, ref = uses.partition("@")
            owner, repo, *_rest = target.split("/")
            calls.append((path.name, job_name, f"{owner}/{repo}", ref))
    return calls


def test_reusable_workflows_are_called_from_a_public_repository():
    """This repository is public, so every reusable workflow it calls must be too.

    Regression guard for the whole reason `release.yml` had zero runs: the
    `authorize` job called the private `portfolio-standards` copy, and GitHub
    answered `workflow was not found` at parse time — before `release-tests`,
    before `build`, before anything that could have reported a real error.
    Nothing in the repository could see it, because a dispatch-only workflow is
    exercised by nothing.
    """
    calls = _reusable_workflow_calls()
    assert calls, (
        "no cross-repository reusable-workflow call was found — if the release "
        "pipeline stopped using one, delete this guard deliberately rather than "
        "letting it pass over nothing"
    )
    for file_name, job_name, repo, _ref in calls:
        assert repo not in PRIVATE_REPOS, (
            f"{file_name}:{job_name} calls a reusable workflow in {repo}, which is "
            "private. A public repository cannot call one, and GitHub reports it "
            "as `workflow was not found` rather than as a permission error, so the "
            "dispatch fails before any job runs."
        )
        assert repo in PUBLIC_REUSABLE_WORKFLOW_REPOS, (
            f"{file_name}:{job_name} calls a reusable workflow in {repo}, which is "
            "not on the confirmed-public list. Confirm the repository is public, "
            "then add it to PUBLIC_REUSABLE_WORKFLOW_REPOS."
        )


def test_reusable_workflow_calls_are_pinned_to_a_full_commit_sha():
    """A tag or branch ref on a reusable workflow is a mutable trust boundary.

    `release-authorize.yml` is what decides whether a signed tag authorizes a
    release; calling it at a moving ref would let the authorization itself be
    rewritten out from under a release without a diff in this repository.
    """
    for file_name, job_name, _repo, ref in _reusable_workflow_calls():
        assert re.fullmatch(r"[0-9a-f]{40}", ref), (
            f"{file_name}:{job_name} calls a reusable workflow at {ref!r}; pin it "
            "to a full 40-character commit SHA"
        )


# ── the corpus-refresh drift report and its gate ─────────────────────────────


def _refresh_step_names() -> list[str]:
    steps = _corpus_freshness_workflow()["jobs"]["refresh"]["steps"]
    return [step.get("name") or f"uses:{step.get('uses', '')}".split("@", 1)[0] for step in steps]


def _refresh_step_index(predicate) -> int:
    steps = _corpus_freshness_workflow()["jobs"]["refresh"]["steps"]
    matches = [i for i, step in enumerate(steps) if predicate(step)]
    assert len(matches) == 1, f"expected exactly one matching step, found {matches}"
    return matches[0]


def test_the_drift_report_is_written_before_the_refresh_pr_is_opened():
    """The report is the PR's own body. Written after, it reaches nobody."""
    report = _refresh_step_index(lambda s: "evals.drift" in str(s.get("run", "")))
    pr = _refresh_step_index(lambda s: str(s.get("uses", "")).startswith("peter-evans/"))
    assert report < pr, (
        "the drift report is appended to /tmp/pr-body.md, which the PR step "
        f"reads: {_refresh_step_names()}"
    )


def test_the_drift_gate_runs_after_the_pr_step_so_the_evidence_ships_first():
    """Ordering, asserted because moving it would silently trade one for the other.

    The drift step itself always exits 0 and appends its report to the PR body.
    The *gate* is a separate step placed after the PR is opened, so a breached
    ceiling turns the run red without preventing the PR that carries the
    explanation. Hoisting the gate above the PR step would keep the verdict and
    throw away the evidence; deleting it would leave a run concluding `success`
    over a ceiling it breached, which removes a signal rather than adding one.
    """
    workflow = _corpus_freshness_workflow()
    steps = workflow["jobs"]["refresh"]["steps"]
    pr = _refresh_step_index(lambda s: str(s.get("uses", "")).startswith("peter-evans/"))
    gate = _refresh_step_index(lambda s: "exit 1" in str(s.get("run", "")))
    assert gate > pr, f"the drift gate must come last: {_refresh_step_names()}"
    assert "steps.drift.outputs.gate" in str(steps[gate].get("if", "")), (
        "the gate step must be conditioned on the drift step's recorded exit code"
    )


def test_the_drift_step_declares_bash_so_its_exit_code_is_its_own():
    """Actions' default `run:` shell is `bash -e {0}` with no `pipefail`.

    This step deliberately captures the drift command's exit status and keeps
    going, so it must not run under `-e`, and it must not read that status
    through a pipe.
    """
    steps = _corpus_freshness_workflow()["jobs"]["refresh"]["steps"]
    step = steps[_refresh_step_index(lambda s: "evals.drift" in str(s.get("run", "")))]
    assert step.get("shell") == "bash"
    run = str(step["run"])
    assert "code=$?" in run, "the drift step must capture the command's own exit status"
    assert "> /tmp/drift.log" in run, (
        "redirect rather than pipe: `$?` after a pipeline is the last command's, "
        "not the one whose verdict is being read"
    )
