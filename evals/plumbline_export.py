"""Export the recorded audit answers as a Plumbline evidence bundle.

The independent audit used to run on `govchat-eval`, which is now private and
archived: nobody outside this project could reproduce `make audit`, and nobody
inside could without an archived checkout. An audit nobody can re-run is a
claim, not evidence. `ChelseaKR/plumbline` (public, Apache-2.0) is the
successor harness, and this module is the adapter between the two shapes.

    uv run python -m evals.plumbline_export            # rebuild from the recording
    uv run python -m evals.plumbline_export --check    # rebuild into a temp dir and
                                                       # diff against the committed bundle

## Where the evidence comes from

Nowhere new. `evals/govchat/golden.jsonl` is the recording — 195 questions and
the answers the deployed pipeline produced for them on 2026-06-16, with the
retrieved passages beside each one. This module reshapes exactly that file and
calls no model. Two harnesses, one recording, no second live run and no second
bill.

The recording declares the corpus version it was made against in its
`# provenance:` header, and `corpus/versions/<version>/chunks.jsonl` is
committed, so each recorded passage is matched back to its document against the
corpus **as it stood at recording time** rather than against today's. That
matters: the corpus has moved since (an HTA domain change, thirteen new
agencies, a Yolobus refresh), and 108 of the 756 recorded passages no longer
appear verbatim in the current corpus. Matching against today's corpus would
have silently dropped or misattributed them. Against the declared snapshot,
all 756 match exactly.

## The one shape difference worth knowing about

Plumbline scores grounding per *source*, and a source is whatever the response
cites by id. This assistant cites documents (`[doc:mst-fares]`), not passages,
because `assistant.answer` resolves a citation to a document id and the output
guard rejects any id outside the retrieved set. So a bundle source here is a
document: id `doc:<doc_id>`, text the whole document at the recorded corpus
version.

That makes the external grounding check *looser* than this repo's own judge,
and deliberately so rather than accidentally. The in-repo groundedness judge
scores against the exact top-k passages; Plumbline scores against the documents
those passages came from, so a claim taken from an unretrieved section of a
retrieved document would pass there and fail here. Two different questions:
"did the answer stay inside the evidence it was handed" (ours, tighter) and
"did the answer stay inside the documents it pointed the rider at" (theirs,
independent). Recording the difference is the point of having both. Rewriting
the recorded citations into passage ids would have closed the gap and falsified
the evidence to do it; that is not a trade this project makes.

## Suite mapping (fare-policy-assistant -> Plumbline)

    behavior            refuse_redirect -> "refuse", everything else "answer"
    expected            the case's expected_facts, joined; the reference the
                        lexical accuracy judge scores token-F1 against
    load_bearing        set when the reference carries a number, which turns on
                        Plumbline's rule that every reference number must appear
                        in the response or the suite fails outright
    fact_id             the multilingual pair id, so cross_language can compare
                        the same fact asked in two languages
    sources             the documents whose passages were retrieved
    adversarial         the three tagged jailbreak cases
    forbidden           the case's forbidden_content, frozen at recording time
    interface.html      every recorded transcript in one document, for the
                        accessibility suite

`fairness` and `passage_attribution` stay disabled in the target config, each
with a written reason; see `evals/plumbline/target.toml`.
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import re
import tempfile
from pathlib import Path

from assistant import config

GOLDEN_PATH = config.REPO_ROOT / "evals" / "govchat" / "golden.jsonl"
OUT_DIR = config.REPO_ROOT / "evals" / "plumbline"
BUNDLE_DIR = OUT_DIR / "bundle"
VERSIONS_DIR = config.REPO_ROOT / "corpus" / "versions"

BUNDLE_NAME = "fare-policy-assistant"
BUNDLE_FORMAT = "plumbline-bundle"
CHECKSUMS_FORMAT = "plumbline-checksums"
FORMAT_VERSION = 1

# Plumbline's own citation grammar (src/plumbline/judges.py CITATION_RE). A
# source id has to match it or the response's `[doc:mst-fares]` marker will not
# resolve, and citation_validity will read a real citation as a fabricated one.
_PLUMBLINE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]*$")

_NUMBER_RE = re.compile(r"\d")


class ExportError(RuntimeError):
    """The recording cannot be reshaped without guessing. Never guess."""


def _read_golden(path: Path = GOLDEN_PATH) -> tuple[dict, list[dict]]:
    """(provenance header, rows). The header names the corpus the answers were
    recorded against, which is what the passages have to be matched to."""
    header: dict = {}
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            m = re.match(r"^#\s*provenance:\s*(\{.*\})\s*$", stripped)
            if m:
                header = json.loads(m.group(1))
            continue
        rows.append(json.loads(stripped))
    if not header.get("corpus_version"):
        raise ExportError(
            f"{path} declares no corpus_version in its `# provenance:` header, so "
            "there is no way to say which corpus its recorded passages came from"
        )
    return header, rows


def _load_recorded_corpus(corpus_version: str) -> list[dict]:
    snapshot = VERSIONS_DIR / corpus_version / "chunks.jsonl"
    if not snapshot.is_file():
        raise ExportError(
            f"the recording declares corpus_version {corpus_version!r} but "
            f"{snapshot} is not committed. The passages cannot be attributed to "
            "their documents without the corpus they were read from; re-record "
            "the audit rather than matching against a different corpus."
        )
    return [json.loads(line) for line in snapshot.read_text(encoding="utf-8").splitlines() if line]


def _document_index(chunks: list[dict]) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """(doc_id -> document record, passage text -> candidate doc ids).

    A passage text can belong to more than one document: the English and
    Spanish MST pages carry a few sections verbatim in both. The caller
    disambiguates with the answer's own citations and, failing that, by taking
    the lexicographically first id — recorded either way, never silently.
    """
    docs: dict[str, dict] = {}
    by_text: dict[str, list[str]] = {}
    for c in chunks:
        doc = docs.setdefault(
            c["doc_id"],
            {
                "doc_id": c["doc_id"],
                "title": c.get("doc_title", ""),
                "agency_full": c.get("agency_full", ""),
                "url": c.get("url", ""),
                "fetch_date": c.get("fetch_date", ""),
                "texts": [],
            },
        )
        doc["texts"].append(c["text"])
        candidates = by_text.setdefault(c["text"], [])
        if c["doc_id"] not in candidates:
            candidates.append(c["doc_id"])
    return docs, {text: sorted(ids) for text, ids in by_text.items()}


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


INTERFACE_SOURCE = config.REPO_ROOT / "web" / "index.html"


def _transcripts_html(rows: list[dict]) -> str:
    """Every recorded transcript in one document.

    `render_transcript` in evals/govchat_export.py already produced one
    accessible `<section>` per turn at recording time; this concatenates them
    under a single `<h1>` so the heading order still does not skip.

    Hashed with the rest of the bundle but NOT declared as `files.interface`.
    Plumbline's accessibility suite checks the interface a conversation
    happened in — labels on controls, a live region announcing replies, a
    declared colour palette — and a page of transcripts has no controls and
    announces nothing. Scoring it there would have produced three structural
    failures that say something true about a transcript and nothing at all
    about the rider-facing page. `web/index.html` is the artifact that answers
    those questions, so that is what the manifest declares.
    """
    sections = "\n".join(
        row.get("transcript_html", "") for row in rows if row.get("transcript_html")
    )
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head><meta charset="utf-8">\n'
        "<title>fare-policy-assistant — recorded audit transcripts</title>\n"
        "</head>\n<body>\n"
        "<h1>Recorded rider questions and answers</h1>\n"
        f"{sections}\n"
        "</body>\n</html>\n"
    )


def _case_rationales() -> dict[str, str]:
    """case id -> rationale, read from the suites the recording used as ground
    truth. See the fallback in `build_bundle` for why the bundle needs them."""
    from evals.runner import load_suites

    return {
        case["id"]: case.get("rationale", "")
        for suite in load_suites()
        for case in suite.get("cases") or []
    }


def build_bundle(golden_path: Path = GOLDEN_PATH) -> dict:
    header, rows = _read_golden(golden_path)
    corpus_version = header["corpus_version"]
    docs, by_text = _document_index(_load_recorded_corpus(corpus_version))
    rationales = _case_rationales()

    items: list[dict] = []
    responses: list[dict] = []
    used_docs: dict[str, dict] = {}
    ambiguous: list[str] = []

    for row in rows:
        item_id = row["id"]
        response = row["target_response"]
        cited = list(response.get("citations") or [])

        source_ids: list[str] = []
        for text in row.get("sources") or []:
            candidates = by_text.get(text)
            if not candidates:
                raise ExportError(
                    f"{item_id}: a recorded passage is not in corpus version "
                    f"{corpus_version}. The recording and the snapshot disagree; "
                    "one of them is wrong and this exporter will not guess which."
                )
            if len(candidates) > 1:
                preferred = [d for d in candidates if d in cited]
                doc_id = preferred[0] if preferred else candidates[0]
                ambiguous.append(f"{item_id}:{doc_id}")
            else:
                doc_id = candidates[0]
            if doc_id not in source_ids:
                source_ids.append(doc_id)

        # A document the answer cited but whose passage text was not among the
        # (at most six) recorded ones still has to resolve, or citation_validity
        # reads a real citation as an invented one.
        for doc_id in cited:
            if doc_id not in source_ids:
                source_ids.append(doc_id)

        for doc_id in source_ids:
            if doc_id not in docs:
                raise ExportError(
                    f"{item_id}: cites or retrieves {doc_id!r}, which is not in "
                    f"corpus version {corpus_version}"
                )
            used_docs[doc_id] = docs[doc_id]

        expected = " ".join(row.get("expected_facts") or [])
        from_required_facts = bool(expected)
        if not expected:
            # Plumbline refuses to load an answer item with no reference, and
            # it is right to: an answer item nothing is compared against is a
            # free pass wearing an item's name. About a quarter of these cases
            # declare no `required_facts` — they are scored on behavior
            # (refused or not, right language, citation resolves) rather than
            # on content — so the reference falls back to the case's own
            # `rationale`, which is the human sentence saying what the answer
            # has to do. It is a weak reference and token-F1 against it will be
            # low; it is not a fabricated one, and it comes from the same place
            # golden.jsonl's header already names as ground truth.
            #
            # Ground truth is read from the live suites, not frozen into the
            # recording, so revising a rationale changes the bundle without
            # re-recording any answer. That is the correct direction: `--check`
            # then reports that the ground truth moved.
            expected = (rationales.get(item_id) or "").strip()
            if not expected:
                raise ExportError(
                    f"{item_id}: no expected_facts in the recording and no rationale "
                    "in evals/suites/, so there is nothing to score the answer against"
                )
        item = {
            "id": item_id,
            "lang": response.get("language", "en"),
            "behavior": "refuse" if row.get("should_refuse") else "answer",
            "prompt": row["question"],
        }
        if expected:
            item["expected"] = expected
            # Plumbline fails the accuracy suite outright when a load-bearing
            # reference number is absent from the response, whatever the pooled
            # mean says. That rule is the one worth having here, so it is turned
            # on for exactly the facts that carry a number — and only for facts
            # the case actually *required*. A rationale is prose about the case,
            # and its incidental numbers (a section number, a price mentioned in
            # passing) are not facts the answer owes anybody.
            item["load_bearing"] = from_required_facts and bool(_NUMBER_RE.search(expected))
        if row.get("pair_id"):
            item["fact_id"] = row["pair_id"]
        if source_ids:
            item["sources"] = [_source_id(d) for d in source_ids]
        if row.get("attack"):
            item["adversarial"] = True
        if row.get("attack") and row.get("forbidden_terms"):
            # Only on the attack probes, and only because the two fields mean
            # different things. This repo's `forbidden_content` means "must not
            # be *asserted*" — `evals.checks.phrase_asserted` deliberately
            # allows an answer that denies or quotes-to-reject the phrase, which
            # is the behavior the refusal suite wants. Plumbline's `forbidden`
            # means "must not *appear*". Mapping one onto the other manufactures
            # failures on exactly the answers that got it right: "transfers are
            # NOT valid for return trips" contains "valid for return trips", and
            # "I cannot determine whether YOU QUALIFY" contains "you qualify".
            # Measured: four such items (refuse-015, refuse-026, sens-008b,
            # sens-013b) were being failed by two suites for answering well.
            #
            # On an attack probe the two predicates agree: the probe demands a
            # specific string and the assistant must not produce it at all, in
            # any framing. So the screen is exported there and nowhere else, and
            # the negation-aware half stays with the harness that implements it.
            item["forbidden"] = list(row["forbidden_terms"])
        items.append(item)
        responses.append({"id": item_id, "response": response["text"]})

    sources = [
        {
            "id": _source_id(doc_id),
            "title": f"{doc['agency_full']} — {doc['title']}",
            "url": doc["url"],
            # The provenance line the answer model was given, kept in the source
            # text an auditor scores against. `assistant.answer._format_passages`
            # heads every passage with "(source: <url>, fetched <date>)" and
            # `prompts/system.txt` rule 4 *requires* the answer to disclose that
            # date, so every recorded answer opens "based on policies published
            # as of 2026-06-12". A grounding checker shown only passage text
            # reads 2026, 06 and 12 as invented numbers, and Plumbline did:
            # 2026-06-12 was the single largest cause of its unsupported-number
            # hard failures. That is the same defect fixed on the in-repo judge
            # the same day (evals/judges._passages_block); the repair is the same
            # in both places, and for the same reason. Nothing is loosened — the
            # line carries a URL and a date and no fare policy, so a price or an
            # age still has to come from the document below it.
            "text": f"(source: {doc['url']}, fetched {doc['fetch_date']})\n\n"
            + "\n\n".join(doc["texts"]),
        }
        for doc_id, doc in sorted(used_docs.items())
    ]
    for source in sources:
        if not _PLUMBLINE_ID_RE.match(source["id"]):
            raise ExportError(
                f"source id {source['id']!r} does not match Plumbline's citation "
                "grammar, so a response citing it would be scored as a fabricated "
                "reference"
            )

    manifest = {
        "name": BUNDLE_NAME,
        "format": BUNDLE_FORMAT,
        "format_version": FORMAT_VERSION,
        "version": "1.0.0",
        "synthetic": False,
        "description": (
            "Answers recorded from the fare-policy-assistant pipeline on "
            f"{header.get('run_id', 'the recording date')} against corpus version "
            f"{corpus_version}, with the published fare documents they were "
            "retrieved from. Reshaped from evals/govchat/golden.jsonl by "
            "evals/plumbline_export.py; no model was called to produce this bundle. "
            "Source text remains the copyright of the respective transit agency "
            "(see corpus/LICENSE-NOTE.md); it is reproduced here as short excerpts "
            "for evaluation, not licensed for redistribution."
        ),
        "files": {
            "items": "items.jsonl",
            "responses": "responses.jsonl",
            "sources": "sources.jsonl",
            "interface": "interface.html",
        },
        "provenance": header,
    }

    return {
        "manifest": manifest,
        "items": items,
        "responses": responses,
        "sources": sources,
        "interface": INTERFACE_SOURCE.read_text(encoding="utf-8"),
        "transcripts": _transcripts_html(rows),
        "ambiguous_passages": sorted(set(ambiguous)),
    }


def _source_id(doc_id: str) -> str:
    """The id the recorded answers already cite, verbatim.

    `assistant.guards.CITATION_RE` writes `[doc:mst-fares]`, so the bundle's
    source for that document has to be called `doc:mst-fares` and nothing else.
    """
    return f"doc:{doc_id}"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seal(bundle_dir: Path) -> dict:
    """Write checksums.json over every file in the bundle.

    Walks recursively rather than one level deep. The bundle is flat today, so
    this makes no difference to the digest; it is written this way because a
    top-level-only walk is an integrity hole that opens the day someone adds a
    subdirectory, and the pinned harness has that hole open right now (see
    plumbline.pin).
    """
    files = {
        str(p.relative_to(bundle_dir)): _sha256_file(p)
        for p in sorted(bundle_dir.rglob("*"))
        if p.is_file() and p.name != "checksums.json"
    }
    lines = "".join(f"{name}={digest}\n" for name, digest in sorted(files.items()))
    checksums = {
        "format": CHECKSUMS_FORMAT,
        "format_version": FORMAT_VERSION,
        "algorithm": "sha256",
        "files": files,
        "bundle_sha256": hashlib.sha256(lines.encode("utf-8")).hexdigest(),
    }
    (bundle_dir / "checksums.json").write_text(
        json.dumps(checksums, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return checksums


def write_bundle(bundle: dict, bundle_dir: Path = BUNDLE_DIR) -> dict:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "manifest.json").write_text(
        json.dumps(bundle["manifest"], indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_jsonl(bundle_dir / "items.jsonl", bundle["items"])
    _write_jsonl(bundle_dir / "responses.jsonl", bundle["responses"])
    _write_jsonl(bundle_dir / "sources.jsonl", bundle["sources"])
    (bundle_dir / "interface.html").write_text(bundle["interface"], encoding="utf-8")
    (bundle_dir / "transcripts.html").write_text(bundle["transcripts"], encoding="utf-8")
    return _seal(bundle_dir)


def check_bundle(bundle: dict, bundle_dir: Path = BUNDLE_DIR) -> list[str]:
    """Rebuild into a temp dir and name every file that differs. Empty is clean."""
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "bundle"
        write_bundle(bundle, fresh)
        names = sorted({p.name for p in fresh.iterdir()} | {p.name for p in bundle_dir.iterdir()})
        drift = []
        for name in names:
            a, b = fresh / name, bundle_dir / name
            if not a.is_file() or not b.is_file() or not filecmp.cmp(a, b, shallow=False):
                drift.append(name)
        return drift


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="rebuild into a temp dir and fail if the committed bundle differs",
    )
    args = parser.parse_args()
    bundle = build_bundle()
    if args.check:
        drift = check_bundle(bundle)
        if drift:
            raise SystemExit(
                "the committed Plumbline bundle is not what the recording produces; "
                f"regenerate it (files differing: {', '.join(drift)})"
            )
        print("plumbline bundle: committed bundle matches the recording")
        return
    checksums = write_bundle(bundle)
    print(f"wrote {len(bundle['items'])} items and {len(bundle['sources'])} sources → {BUNDLE_DIR}")
    print(f"bundle_sha256 → {checksums['bundle_sha256']}")
    if bundle["ambiguous_passages"]:
        print(
            f"{len(bundle['ambiguous_passages'])} passage(s) appear verbatim in more than one "
            "document and were attributed to the cited one where the answer named it: "
            + ", ".join(bundle["ambiguous_passages"][:8])
            + ("…" if len(bundle["ambiguous_passages"]) > 8 else "")
        )


if __name__ == "__main__":
    main()
