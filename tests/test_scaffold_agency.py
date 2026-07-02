"""Tests for the 'add an agency' contribution kit (assistant.scaffold_agency).

These exercise the pure builders on a fake chunks list and tmp_path outputs (no
network, no corpus dependency), and assert the safety rail: the runner refuses a
suite that still carries a `draft: true` case.
"""

from __future__ import annotations

import pytest
import yaml

from assistant import config, scaffold_agency
from assistant.ingest import Chunk
from evals import runner

FAKE_CHUNKS = [
    {
        "chunk_id": "hta-fares#0",
        "agency": "HTA",
        "agency_full": "Humboldt Transit Authority",
        "language": "en",
        "text": 'Single ride is $1.75. A "day pass" costs $3.50.\nSeniors ride for half fare.',
    },
    {
        "chunk_id": "hta-fares#1",
        "agency": "HTA",
        "agency_full": "Humboldt Transit Authority",
        "language": "es",
        "text": "Tarifa reducida solo con pases de valor almacenado.",
    },
]


# ── manifest stanza ──────────────────────────────────────────────────────────


def test_manifest_stanza_matches_manifest_format():
    stanza = scaffold_agency.build_manifest_stanza(
        "hta", "Humboldt Transit Authority", "https://humboldttransit.org/fares/"
    )
    assert "  - id: hta-fares" in stanza
    assert "    agency: HTA" in stanza
    assert "    agency_full: Humboldt Transit Authority" in stanza
    assert '    title: "Fares"' in stanza
    assert "    url: https://humboldttransit.org/fares/" in stanza
    assert "    language: en" in stanza
    assert "license_note:" in stanza
    # robots/permissions reminder is present.
    assert "robots.txt" in stanza
    # The stanza (minus its leading comments) parses as one manifest document.
    body = "\n".join(
        line for line in stanza.splitlines() if not line.lstrip().startswith("#")
    )
    docs = yaml.safe_load(body)
    assert docs[0]["id"] == "hta-fares"
    assert docs[0]["agency"] == "HTA"


def test_manifest_stanza_uses_id_and_url_overrides():
    stanza = scaffold_agency.build_manifest_stanza(
        "SacRt", "Sacramento Regional Transit", "https://x/", language="es"
    )
    assert "  - id: sacrt-fares" in stanza
    assert "    agency: SACRT" in stanza
    assert "    language: es" in stanza


def test_append_manifest_stanza_writes_commented_block(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    original = "documents:\n  - id: existing\n"
    manifest.write_text(original, encoding="utf-8")
    stanza = scaffold_agency.build_manifest_stanza("hta", "HTA Full", "https://x/")
    scaffold_agency.append_manifest_stanza(stanza, manifest)
    text = manifest.read_text(encoding="utf-8")
    assert text.startswith(original)
    # Every appended line is commented out, so the live manifest is unchanged
    # until a human uncomments it.
    appended = text[len(original) :]
    assert "scaffolded by" in appended
    for line in appended.splitlines():
        if line.strip():
            assert line.lstrip().startswith("#")
    # And the original document set still parses (the comments are inert).
    assert yaml.safe_load(text)["documents"][0]["id"] == "existing"


# ── draft eval-case skeletons ────────────────────────────────────────────────


def test_render_draft_suite_one_case_per_chunk():
    text = scaffold_agency.render_draft_suite("hta", FAKE_CHUNKS)
    data = yaml.safe_load(text)
    assert data["suite"] == "draft_hta"
    assert len(data["cases"]) == len(FAKE_CHUNKS)

    first = data["cases"][0]
    assert first["id"] == "hta-draft-001"
    assert first["draft"] is True
    assert first["agency_scope"] == "HTA"
    assert first["language"] == "en"
    assert first["expected_behavior"] == "answer"
    assert first["required_facts"] == []
    assert "TODO" in first["question"]
    # The source passage is inline as the rationale, verbatim including the
    # double quotes and the newline, so a literal block scalar was used.
    assert '"day pass"' in first["rationale"]
    assert "Seniors ride for half fare." in first["rationale"]

    # Ids are zero-padded and sequential; per-chunk language carries through.
    assert data["cases"][1]["id"] == "hta-draft-002"
    assert data["cases"][1]["language"] == "es"


def test_write_draft_suite_writes_file(tmp_path):
    path = scaffold_agency.write_draft_suite("hta", FAKE_CHUNKS, tmp_path)
    assert path == tmp_path / "draft_hta.yaml"
    assert path.exists()
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["suite"] == "draft_hta"


# ── parity checklist ─────────────────────────────────────────────────────────


def test_write_checklist_creates_markdown(tmp_path):
    path = scaffold_agency.write_checklist("hta", "Humboldt Transit Authority", tmp_path)
    assert path == tmp_path / "hta-checklist.md"
    body = path.read_text(encoding="utf-8")
    assert "Humboldt Transit Authority" in body
    assert "robots.txt" in body
    assert "Spanish" in body
    # Parity mirror plan names the real suites.
    for suite in ("groundedness", "refusal", "multilingual", "freshness"):
        assert suite in body
    assert "draft: true" in body  # the "remove the flags" step
    assert "make verify" in body


# ── the safety rail: the runner refuses a draft case ─────────────────────────


def test_runner_validate_cases_rejects_a_draft_case():
    suites = [
        {
            "cases": [
                {
                    "id": "hta-draft-001",
                    "draft": True,
                    "question": "TODO",
                    "agency_scope": "HTA",
                    "language": "en",
                    "expected_behavior": "answer",
                    "required_facts": [],
                    "rationale": "some passage",
                }
            ]
        }
    ]
    with pytest.raises(SystemExit, match="draft"):
        runner.validate_cases(suites)


def test_runner_validate_cases_accepts_the_same_case_once_unflagged():
    # Removing the draft flag (and giving it a real question) makes it valid.
    suites = [
        {
            "cases": [
                {
                    "id": "hta-001",
                    "question": "How much is a single ride on HTA?",
                    "agency_scope": "HTA",
                    "language": "en",
                    "expected_behavior": "answer",
                    "required_facts": ["$1.75"],
                    "rationale": "some passage",
                }
            ]
        }
    ]
    runner.validate_cases(suites)  # does not raise


# ── main() entry point ───────────────────────────────────────────────────────


def _fake_chunk(agency="HTA", language="en", text="Single ride $1.75."):
    return Chunk(
        chunk_id="hta-fares#0",
        doc_id="hta-fares",
        agency=agency,
        agency_full="Humboldt Transit Authority",
        doc_title="Fares",
        url="https://x/",
        fetch_date="2026-07-02",
        language=language,
        section="Fares",
        text=text,
    )


@pytest.fixture
def _redirect_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MANIFEST_PATH", tmp_path / "manifest.yaml")
    monkeypatch.setattr(config, "EVAL_SUITES_DIR", tmp_path / "suites")
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    config.MANIFEST_PATH.write_text("documents: []\n", encoding="utf-8")
    return tmp_path


def test_main_with_chunks_writes_all_three_artifacts(_redirect_outputs, monkeypatch, capsys):
    monkeypatch.setattr(scaffold_agency, "load_chunks", lambda: [_fake_chunk()])
    scaffold_agency.main(["hta", "--agency-full", "Humboldt Transit Authority", "--write"])
    out = capsys.readouterr().out
    assert "  - id: hta-fares" in out  # stanza on stdout

    manifest = (_redirect_outputs / "manifest.yaml").read_text(encoding="utf-8")
    assert "scaffolded by" in manifest  # appended (commented) by --write

    suite = _redirect_outputs / "suites" / "draft_hta.yaml"
    assert yaml.safe_load(suite.read_text(encoding="utf-8"))["cases"][0]["draft"] is True

    assert (_redirect_outputs / "docs" / "agencies" / "hta-checklist.md").exists()


def test_main_without_chunks_still_emits_stanza(_redirect_outputs, monkeypatch, capsys):
    monkeypatch.setattr(scaffold_agency, "load_chunks", lambda: [_fake_chunk(agency="MST")])
    scaffold_agency.main(["hta"])  # no HTA chunks → no draft suite
    assert "  - id: hta-fares" in capsys.readouterr().out
    assert not (_redirect_outputs / "suites" / "draft_hta.yaml").exists()
    assert (_redirect_outputs / "docs" / "agencies" / "hta-checklist.md").exists()
