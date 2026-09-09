"""Two published claims about *our* ingest that are really claims about a source.

Between 2026-07 and 2026-09 this repository told five audiences — the README,
the procurement brief, the audit methodology, a comment on ``evals.checks``'s
clock-time regex, and 23 entries in ``evals/plumbline/acknowledged_findings.json``
— that "the corpus cleaner broke" the MTD Business Office number into
``805. 963.3364``. ``evals/plumbline/target.toml`` went further and parked the
``privacy`` floor below the measurement, with the remediation written down as "a
corpus reprocess and a re-recording".

Neither half was true. SBMTD publishes the space, and so does e-tran's
``1.a.m.``; the ingest path reproduced both faithfully. So the stated
remediation could never fire: a reprocess reads the same bytes and writes the
same bytes, forever. A waiver whose clearing condition is impossible is a
permanent exemption wearing a temporary one's clothes, and nothing in the tree
could tell the difference — because nothing had read the raw document since the
day it was fetched.

These tests make the corrected claim a function of committed bytes:

1. the raw file is the document the manifest says was fetched (sha256), so a
   later refetch cannot silently change what "the source" means underneath the
   prose;
2. the source spells the string the way the prose now says it does;
3. the processed corpus carries the *same* spelling, which is the half that
   makes it a statement about the ingest rather than only about the agency.

If any of these goes red, the finding may genuinely have cleared — an agency
does sometimes fix its own page — and the sentences in ``README.md``,
``docs/procurement-brief.md``, ``docs/audits/methodology.md``,
``evals/checks.py`` and ``evals/plumbline/target.toml`` have to be re-read, not
the test relaxed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pytest
import yaml

from assistant import config

RAW_DIR = config.CORPUS_DIR / "raw"
PROCESSED_DIR = config.CORPUS_DIR / "processed"


@dataclass(frozen=True)
class SourceSpelling:
    """One irregular spelling this repository publishes a sentence about."""

    doc_id: str
    #: Exactly as the agency published it, and as the corrected prose quotes it.
    published: str
    #: What a reader would expect instead. Asserted *absent* from the raw file
    #: at the point the published form appears, so "the source spells it oddly"
    #: cannot pass on a document that spells it both ways in the same breath.
    expected_by_a_reader: str
    #: Where the claim about it is made, for the failure message.
    claimed_in: str


SPELLINGS = (
    SourceSpelling(
        doc_id="sbmtd-fares-passes",
        published="805. 963.3364",
        expected_by_a_reader="805.963.3364",
        claimed_in="evals/plumbline/target.toml [suites.privacy], and 23 entries "
        "in evals/plumbline/acknowledged_findings.json",
    ),
    SourceSpelling(
        doc_id="etran-fares",
        published="1.a.m.",
        expected_by_a_reader="1 a.m.",
        claimed_in="the _CLOCK_RE comment in evals/checks.py",
    ),
)


def _raw_text(doc_id: str) -> str:
    """The fetched document, after proving it is the one the manifest declares.

    The hash check is the floor. Without it this whole module reads a file that
    a refresh may have replaced, and "the source publishes the space" would be a
    claim about whatever happens to be on disk — which is the same defect, one
    layer down, as the prose it exists to hold up.
    """
    raw = RAW_DIR / f"{doc_id}.html"
    meta_path = RAW_DIR / f"{doc_id}.meta.yaml"
    assert raw.is_file(), f"{raw} is missing; this module cannot check a document it cannot read"
    assert meta_path.is_file(), f"{meta_path} is missing, so the raw file's identity is unpinned"

    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    declared = meta.get("sha256")
    assert declared, f"{meta_path} declares no sha256"

    data = raw.read_bytes()
    assert data, f"{raw} is empty"
    actual = hashlib.sha256(data).hexdigest()
    assert actual == declared, (
        f"{raw} hashes to {actual} but {meta_path.name} declares {declared}. The raw "
        f"document has changed since it was fetched, so every sentence in this "
        f"repository about what its source publishes needs re-reading before this "
        f"test is adjusted."
    )
    return data.decode("utf-8", errors="strict")


@pytest.mark.parametrize("spelling", SPELLINGS, ids=lambda s: s.doc_id)
def test_the_irregular_spelling_is_the_agencys_and_not_this_repositorys(
    spelling: SourceSpelling,
) -> None:
    """The fetched page publishes it, and the processed corpus reproduces it.

    Both halves are needed. The first alone leaves open that the ingest
    *introduced* the same artifact independently; the second alone is what the
    old, wrong sentence was written from — somebody read
    ``corpus/processed/*.md``, saw a spelling no reader would choose, and
    attributed it to the only step between the web and that file.
    """
    raw = _raw_text(spelling.doc_id)
    assert spelling.published in raw, (
        f"corpus/raw/{spelling.doc_id}.html no longer publishes {spelling.published!r}. "
        f"{spelling.claimed_in} says the agency publishes it and this project did not "
        f"introduce it. If the agency corrected its page, that finding may now clear on "
        f"a refresh — say so there rather than deleting this case."
    )

    processed = (PROCESSED_DIR / f"{spelling.doc_id}.md").read_text(encoding="utf-8")
    assert processed, f"corpus/processed/{spelling.doc_id}.md is empty"
    assert spelling.published in processed, (
        f"corpus/processed/{spelling.doc_id}.md no longer carries {spelling.published!r} "
        f"while corpus/raw/{spelling.doc_id}.html still does. The ingest has started "
        f"normalising the source, which is a change of posture: it makes the processed "
        f"corpus disagree with the document it cites. That may be right, and it makes "
        f"{spelling.claimed_in} wrong."
    )


@pytest.mark.parametrize("spelling", SPELLINGS, ids=lambda s: s.doc_id)
def test_the_reader_spelling_is_not_also_there_in_the_same_place(
    spelling: SourceSpelling,
) -> None:
    """The other half of the claim, and the one that keeps it falsifiable.

    ``sbmtd-fares-passes.html`` spells the *other* Business Office number both
    ways — ``805. 963.3366`` in the address block and ``805.963.3366`` in a
    later sentence — which is exactly why "the string appears somewhere in the
    document" proves nothing here. This pins that the surrounding text of the
    flagged occurrence carries only the irregular form, so an exact-substring
    check really has nothing to find.
    """
    raw = _raw_text(spelling.doc_id)
    index = raw.index(spelling.published)
    window = raw[max(0, index - 200) : index + 200]
    assert spelling.expected_by_a_reader not in window, (
        f"corpus/raw/{spelling.doc_id}.html now carries {spelling.expected_by_a_reader!r} "
        f"within 200 characters of {spelling.published!r}. An exact-substring check would "
        f"find it there, so the acknowledgement built on 'the suite cannot find it in the "
        f"source' needs re-reading."
    )


def test_the_probe_can_report_a_document_that_does_not_publish_the_spelling() -> None:
    """A positive control, against a synthetic document rather than a real one.

    Without it, "the source publishes the space" is indistinguishable from a
    predicate that says yes to everything — and it has to keep meaning the same
    thing on the day SBMTD republishes the page, which is precisely when the
    real fixture stops being able to demonstrate it.
    """
    clean = "550 Olive Street<br>Santa Barbara, CA 93101<br>805.963.3364<br>"
    assert "805. 963.3364" not in clean
    assert "805.963.3364" in clean
