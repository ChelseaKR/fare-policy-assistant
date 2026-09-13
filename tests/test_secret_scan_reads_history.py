"""The required `Secret scan (gitleaks)` check must read every commit.

`Secret scan (gitleaks)` is a required status check on `main`. Until 2026-09-13
the job ran `gitleaks/gitleaks-action`, which chooses its scan range from the
triggering event:

    push           gitleaks detect --log-opts=--no-merges --first-parent BASE^..HEAD
    push (1 commit) gitleaks detect --log-opts=-1          <- exactly one commit
    pull_request   the pull request's own commits

Every squash merge into `main` is a one-commit push, and `security.yml` has
neither a `schedule` nor a `workflow_dispatch` trigger, so no run of it had ever
read more than 1 of `main`'s 279 commits. A credential added in one commit and
deleted in the next was invisible to it.

`fetch-depth: 0` did not prevent that and cannot: it decides how much history
`actions/checkout` puts on disk, not how much of it the scanner is asked to
read. A checkout deep enough to scan and an invocation that declines to is
exactly the state this repository was in, and the comment on that line said the
opposite. So the assertions below are about the *invocation*; the `fetch-depth:
0` assertion is kept only as the necessary precondition it actually is.

Measured on a throwaway clone of this repository, remote removed, with the fix
in hand: a random real-shaped AWS key planted in one commit and removed in the
next left `gitleaks git . --log-opts=-1` exiting 0, while `gitleaks git .`
exited 1.
"""

from __future__ import annotations

import re
from pathlib import Path

# Resolved from this file, deliberately, and not from `assistant.config.REPO_ROOT`.
# That constant is computed from the INSTALLED package, so in a git worktree whose
# venv points at the primary checkout it names the other tree: the assertions below
# then read a `security.yml` this branch never touched and fail — or, worse, pass on
# a file the change did not edit. A test about a file in this working tree has to
# find it from this working tree.
SECURITY = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "security.yml"

# Four conformance checks elsewhere in this portfolio passed because they matched
# a tool name inside a COMMENT. The comment above the scan step names both the
# action that was removed and the flag that must not return, so every assertion
# below reads the file with its comments stripped and cannot be satisfied by prose.
_COMMENT = re.compile(r"(?m)^\s*#.*$|\s+#.*$")


def _security_code() -> str:
    return _COMMENT.sub("", SECURITY.read_text(encoding="utf-8"))


def test_the_scanner_is_not_handed_a_range() -> None:
    text = _security_code()
    assert "gitleaks git . --no-banner --redact --exit-code 1" in text, (
        "the secret scan no longer runs `gitleaks git .`. Whatever replaces it must "
        "still walk every commit reachable from HEAD, on every event."
    )
    assert "--log-opts" not in text, (
        "`--log-opts` scopes gitleaks to a commit range. A range chosen from the "
        "triggering event is how this check came to read 1 of main's 279 commits."
    )


def test_the_event_driven_action_does_not_come_back() -> None:
    assert "gitleaks/gitleaks-action" not in _security_code(), (
        "gitleaks/gitleaks-action picks its range from the event and degrades to "
        "`--log-opts=-1` on a single-commit push, which every squash merge here is."
    )


def test_checkout_still_fetches_the_history_the_scan_walks() -> None:
    """Necessary, not sufficient: without it there is nothing on disk to walk."""
    text = _security_code()
    assert re.search(r"^\s*fetch-depth:\s*0\s*$", text, flags=re.MULTILINE), (
        "`fetch-depth: 0` is gone from the secret-scan checkout, so `gitleaks git .` "
        "would walk only the single commit actions/checkout fetched. This is the "
        "precondition for a history scan; the invocation is what makes it one."
    )


def test_the_pinned_binary_is_checksum_verified() -> None:
    text = _security_code()
    assert "gitleaks_checksums.txt" in text and "sha256sum --check --strict" in text, (
        "the gitleaks binary is downloaded without verifying its published checksum"
    )


def test_the_required_check_context_is_unchanged() -> None:
    """`Secret scan (gitleaks)` is a required status check on `main`; renaming the
    job or its display name would block every pull request rather than fix one."""
    text = _security_code()
    assert "\n  secret-scan:\n" in text, "the secret-scan job id changed"
    assert "\n    name: Secret scan (gitleaks)\n" in text, (
        "the secret-scan job's display name changed; it is a required status-check "
        "context on main and must stay byte-identical"
    )
