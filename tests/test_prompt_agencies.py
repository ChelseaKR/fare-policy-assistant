"""The system prompt's agency list must match the corpus.

This is the guard for a failure that actually happened. Between 2026-08-12 and
2026-08-13 four agencies were added to `corpus/manifest.yaml` (E-tran, SCMTD,
SolTrans, FAX) while `prompts/system.txt` still named five and instructed the
model to decline "an agency, place, or service outside the five agencies above".
Every gate in the repository was green throughout: the corpus was valid, the
eval suites passed offline, coverage held. Nothing compared the two files,
because nothing ever had.

The consequence is quiet and bad. A rider asks about a corpus agency, retrieval
finds the passages, and the model declines anyway because its instructions say
that agency is out of scope. The corpus grows, the assistant does not, and no
test notices.
"""

from __future__ import annotations

import yaml

from assistant import config


def _manifest_agencies() -> dict[str, str]:
    """Every agency in the corpus, as {short code: full name}."""
    manifest = yaml.safe_load(config.MANIFEST_PATH.read_text(encoding="utf-8"))
    agencies: dict[str, str] = {}
    for doc in manifest["documents"]:
        agencies.setdefault(doc["agency"], doc.get("agency_full", ""))
    return agencies


def test_every_corpus_agency_is_named_in_the_system_prompt() -> None:
    prompt = config.load_prompt("system")
    missing = [code for code in _manifest_agencies() if code not in prompt]
    assert not missing, (
        f"{missing} are in the corpus but not named in prompts/system.txt. "
        "The prompt tells the model to decline anything outside the agencies it "
        "lists, so these agencies' passages would be retrieved and then refused."
    )


def test_the_scope_rule_does_not_hardcode_a_count() -> None:
    """A number in the scope rule is a second thing to forget.

    The rule used to read "outside the five agencies above". That count went
    stale the moment a sixth agency landed, and it is not the kind of staleness
    anything reports.
    """
    # Skip line 1: prompt_version() reads it, and it quotes the superseded
    # phrasing on purpose while recording why the wording changed.
    body = "\n".join(config.load_prompt("system").splitlines()[1:])
    for number in (
        "five agencies",
        "six agencies",
        "seven agencies",
        "eight agencies",
        "nine agencies",
        "ten agencies",
    ):
        assert number not in body, (
            f'system.txt says "{number}"; use "the agencies listed above" so the '
            "sentence cannot go stale when the corpus grows."
        )
