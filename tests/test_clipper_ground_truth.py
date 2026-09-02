"""`xagency-010`'s ground truth must stay true of the corpus it is scored against.

Issue #162. The case's rationale described the six-agency corpus — "SolTrans is
the only Clipper participant documented in this corpus" — long after the corpus
had grown to eighteen agencies and seven of them documented Clipper acceptance in
their own passages. `judge_helpfulness` v3 is threaded the rationale by design, so
it used the stale text as ground truth and reported an answer that correctly
enumerated the corpus as fabricating citations. The case was unpassable by
construction: the only answer that agreed with the rationale was wrong about the
corpus, and the only answer right about the corpus failed the judge.

Nothing made that visible. The rationale was prose, the corpus was data, and they
drifted for two weeks with every gate green. This module is what makes them fail
together: it asserts the per-agency Clipper claims the rewritten rationale makes,
against `corpus/processed/chunks.jsonl` at HEAD. An agency that starts or stops
documenting Clipper fails here, in CI, offline, rather than silently in a live
judge's reasoning weeks later.
"""

from __future__ import annotations

import json
import re

import pytest

from assistant import config
from evals.checks import fact_matches, phrase_asserted
from evals.runner import load_suites

# The three-way split the rewritten rationale states, agency by agency.
DOCUMENTED_ACCEPTORS = {
    "AC Transit",
    "CCCTA",
    "Marin Transit",
    "SamTrans",
    "SolTrans",
    "VTA",
    "WestCAT",
}
DOCUMENTED_NON_ACCEPTOR = "SCMTD"
# vine-fares documents a Clipper START discount applying to Vine fares without
# saying Clipper is accepted on board. The rationale calls it out as the boundary
# case and requires it neither way; it is neither an acceptor nor silent.
INDIRECT = "VINE"
SILENT = {
    "E-tran",
    "FAX",
    "HTA",
    "MST",
    "SBMTD",
    "SJRTD",
    "SLORTA",
    "SacRT",
    "Yolobus",
}

CLIPPER = re.compile(r"clipper", re.I)


@pytest.fixture(scope="module")
def chunks() -> list[dict]:
    return [
        json.loads(line)
        for line in config.CHUNKS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture(scope="module")
def case() -> dict:
    for suite in load_suites():
        for entry in suite.get("cases") or []:
            if entry.get("id") == "xagency-010":
                return entry
    raise AssertionError("xagency-010 is not in any suite")


def _clipper_chunks(chunks: list[dict], agency: str) -> list[dict]:
    return [
        chunk
        for chunk in chunks
        if chunk["agency"] == agency
        and (CLIPPER.search(chunk["text"]) or CLIPPER.search(chunk.get("doc_title", "")))
    ]


class TestTheGroundTruthMatchesTheCorpus:
    def test_the_split_covers_every_agency_exactly_once(self, chunks):
        classified = DOCUMENTED_ACCEPTORS | {DOCUMENTED_NON_ACCEPTOR, INDIRECT} | SILENT
        assert classified == {chunk["agency"] for chunk in chunks}
        assert len(classified) == len(DOCUMENTED_ACCEPTORS) + 2 + len(SILENT)

    @pytest.mark.parametrize("agency", sorted(DOCUMENTED_ACCEPTORS))
    def test_each_named_acceptor_has_a_passage_behind_it(self, chunks, agency):
        assert _clipper_chunks(chunks, agency), (
            f"the rationale names {agency} as documenting Clipper acceptance, but no "
            f"chunk of {agency}'s mentions Clipper"
        )

    def test_santa_cruz_metro_documents_the_refusal_verbatim(self, chunks):
        text = " ".join(chunk["text"] for chunk in _clipper_chunks(chunks, DOCUMENTED_NON_ACCEPTOR))
        assert "Clipper Cards are not honored on METRO buses" in text

    def test_vine_documents_clipper_start_without_documenting_acceptance(self, chunks):
        text = " ".join(chunk["text"] for chunk in _clipper_chunks(chunks, INDIRECT))
        assert "Clipper START" in text
        assert not re.search(r"\bVine\b[^.]{0,40}\b(accepts|takes|honors)\b[^.]{0,25}Clipper", text)

    @pytest.mark.parametrize("agency", sorted(SILENT))
    def test_each_silent_agency_really_is_silent(self, chunks, agency):
        assert _clipper_chunks(chunks, agency) == [], (
            f"the rationale states the corpus says nothing about {agency} and Clipper, "
            f"but {agency} now has a chunk mentioning it — rewrite the rationale before "
            f"the judge is scored against it again"
        )


class TestTheCaseChecksWhatTheRationaleSays:
    def test_every_documented_acceptor_is_a_required_fact(self, case):
        answer = "Clipper is documented on " + ", ".join(sorted(DOCUMENTED_ACCEPTORS)) + "."
        # `CCCTA` and `VTA` are spelled out in real answers; the required facts
        # accept either form, so check the long names too.
        answer += (
            " County Connection, Santa Clara Valley Transportation Authority, "
            "and Santa Cruz METRO are also named."
        )
        missing = [fact for fact in case["required_facts"] if not fact_matches(fact, answer)]
        assert missing == []

    def test_an_answer_that_names_only_soltrans_now_fails(self, case):
        """The old ground truth's "correct" answer is the current failure."""

        answer = (
            "SolTrans is the only agency in this corpus documented as taking Clipper. "
            "The corpus does not establish Clipper status for the others."
        )
        missing = [fact for fact in case["required_facts"] if not fact_matches(fact, answer)]
        assert missing, "an answer naming only SolTrans should no longer satisfy this case"

    @pytest.mark.parametrize(
        "answer",
        [
            "SacRT accepts Clipper for all fares.",
            "SBMTD takes Clipper on every route.",
            "Santa Cruz METRO accepts Clipper.",
            "MST accepts Clipper.",
            "Yolobus accepts Clipper.",
        ],
    )
    def test_inventing_acceptance_is_caught(self, case, answer):
        assert [
            phrase for phrase in case["forbidden_content"] if phrase_asserted(phrase, answer)
        ], f"forbidden_content did not catch: {answer}"

    @pytest.mark.parametrize(
        "answer",
        [
            "The corpus does not say whether SacRT accepts Clipper.",
            "No passage says MST accepts Clipper, so its status is not established.",
            "Clipper Cards are not honored on Santa Cruz METRO buses.",
            "SolTrans, AC Transit, County Connection, WestCAT, Marin Transit, SamTrans "
            "and VTA each document Clipper acceptance in their own fare pages.",
        ],
    )
    def test_correct_answers_are_not_caught(self, case, answer):
        assert [
            phrase for phrase in case["forbidden_content"] if phrase_asserted(phrase, answer)
        ] == []

    def test_every_pattern_in_the_case_compiles(self, case):
        for entry in case["required_facts"] + case["forbidden_content"]:
            fact_matches(entry, "probe")

    def test_the_rationale_no_longer_claims_soltrans_is_the_only_participant(self, case):
        assert "only Clipper participant" not in case["rationale"]
        for agency in sorted(DOCUMENTED_ACCEPTORS):
            assert agency in case["rationale"] or agency in ("CCCTA", "VTA")
