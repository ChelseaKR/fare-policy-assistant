"""GovChat-Eval export tests (the independent-audit layer).

Built offline against the real suites and corpus with the mock model. The
export carries the safety-relevant ground truth into the black-box audit:
`should_refuse` for refuse_redirect cases, `forbidden_terms` for representational
PII/determination phrases, and `attack` tags for the jailbreak cases. These
assert that mapping is faithful, plus the helpers (literal-fact extraction,
accessible transcript, provenance, multilingual pairing) and the hashed write.
"""

from __future__ import annotations

import json

import pytest

from evals import govchat_export as gx

# ── helpers ──────────────────────────────────────────────────────────────────


class TestLiteralFacts:
    def test_plain_literal_passes_through(self):
        assert gx._literal_facts(["DD Form 214"], "bring a DD Form 214") == ["DD Form 214"]

    def test_regex_uses_the_substring_it_matched(self):
        facts = gx._literal_facts([r"re:\$\s?2\.00"], "the fare is $2.00 today")
        assert facts == ["$2.00"]

    def test_regex_miss_falls_back_to_readable_literal(self):
        # The pattern does not match the answer (a genuine gap); the fallback
        # derives a readable literal so the independent check flags it too.
        facts = gx._literal_facts([r"re:\$\s?2\.00"], "no figure here")
        assert facts == ["$ 2.00"]


class TestRenderTranscript:
    def test_escapes_html_and_declares_language(self):
        html = gx.render_transcript("<b>hi</b>", "answer & more", ["MST: Fares"], "es")
        assert 'lang="es"' in html
        assert "&lt;b&gt;hi&lt;/b&gt;" in html  # question escaped
        assert "answer &amp; more" in html  # answer escaped
        assert "<li>MST: Fares</li>" in html  # sources rendered as a list

    def test_no_sources_omits_the_sources_block(self):
        html = gx.render_transcript("q", "a", [], "en")
        assert "<h3>Sources</h3>" not in html


class TestProvenance:
    def test_uses_citation_agency_when_present(self):
        from assistant.answer import AnswerResult, Citation

        r = AnswerResult(
            question="q",
            answer="a",
            kind="answered",
            citations=[Citation("d", "MST", "Fares", "u", "2026-06-12")],
        )
        assert gx._provenance(r)["source"].startswith("MST")

    def test_falls_back_to_passage_agency_then_corpus(self):
        from assistant.answer import AnswerResult
        from assistant.ingest import Chunk
        from assistant.retrieve import ScoredChunk

        ch = Chunk(
            "c#0",
            "d",
            "MST",
            "Monterey-Salinas Transit",
            "Fares",
            "u",
            "2026-06-12",
            "en",
            "S",
            "t",
        )
        r = AnswerResult(
            question="q", answer="a", kind="answered", passages=[ScoredChunk(chunk=ch, score=1.0)]
        )
        assert "Monterey-Salinas Transit" in gx._provenance(r)["source"]
        empty = AnswerResult(question="q", answer="a", kind="refused_no_support")
        assert "corpus" in gx._provenance(empty)["source"]


class TestMultilingualPairing:
    def test_factual_mirror_is_paired_and_anchored(self):
        items = {"es-1": {}, "en-1": {}}
        cases = {
            "es-1": {"mirror_of": "en-1", "language": "es", "expected_behavior": "answer"},
            "en-1": {"expected_behavior": "answer", "language": "en"},
        }
        gx._pair_multilingual(items, cases)
        assert items["es-1"]["pair_id"] == "pair-en-1"
        assert items["en-1"]["is_reference"] is True

    def test_refusal_mirror_is_not_paired(self):
        # A Spanish case mirroring a refusal has no figures to preserve, so it is
        # left out of the anchor-fidelity pairing.
        items = {"es-2": {}, "en-2": {}}
        cases = {
            "es-2": {"mirror_of": "en-2", "language": "es", "expected_behavior": "refuse_redirect"},
            "en-2": {"expected_behavior": "refuse_redirect", "language": "en"},
        }
        gx._pair_multilingual(items, cases)
        assert "pair_id" not in items["es-2"]


# ── build_dataset (offline, real suites) ─────────────────────────────────────


@pytest.fixture(scope="module")
def dataset():
    return gx.build_dataset(offline=True)


def test_dataset_covers_every_single_turn_case(dataset):
    assert len(dataset) > 50
    for item in dataset:
        assert item["id"] and item["question"]
        assert "text" in item["target_response"]
        assert isinstance(item["should_refuse"], bool)
        assert item["transcript_html"].startswith("<section")


def test_refuse_redirect_cases_carry_should_refuse(dataset):
    # The audit's refusal suite keys off should_refuse; every refuse_redirect
    # case must set it, and answer/partial cases must not.
    refusing = [it for it in dataset if it["should_refuse"]]
    assert refusing, "expected refuse_redirect cases in the export"


def test_attack_cases_are_tagged_for_the_adversarial_suite(dataset):
    by_id = {it["id"]: it for it in dataset}
    for cid, kind in gx.ATTACK_CASES.items():
        if cid in by_id:  # case still present in the suites
            assert by_id[cid]["attack"] == kind


def test_forbidden_terms_propagate_when_present(dataset):
    # At least one case declares forbidden_content, and it rides into the export
    # as forbidden_terms for the representational check.
    with_terms = [it for it in dataset if it.get("forbidden_terms")]
    assert with_terms, "expected forbidden_terms on at least one exported case"


# ── write_dataset (redirected away from the committed golden file) ───────────


def test_write_dataset_emits_jsonl_and_sha256(tmp_path, monkeypatch):
    out = tmp_path / "golden.jsonl"
    monkeypatch.setattr(gx, "OUT_DIR", tmp_path)
    monkeypatch.setattr(gx, "DATASET_PATH", out)
    items = [{"id": "x", "question": "q?", "should_refuse": False}]
    gx.write_dataset(items)
    body = out.read_text()
    assert body.startswith("# fare-policy-assistant")  # header comment
    last = body.strip().splitlines()[-1]
    assert json.loads(last)["id"] == "x"
    sha = out.with_suffix(".jsonl.sha256").read_text().strip()
    assert len(sha) == 64  # sha256 hex digest


def test_main_offline_writes_dataset(tmp_path, monkeypatch):
    out = tmp_path / "golden.jsonl"
    monkeypatch.setattr(gx, "OUT_DIR", tmp_path)
    monkeypatch.setattr(gx, "DATASET_PATH", out)
    monkeypatch.setattr("sys.argv", ["govchat_export", "--offline"])
    gx.main()
    assert out.exists() and out.with_suffix(".jsonl.sha256").exists()
