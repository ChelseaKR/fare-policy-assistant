"""Export the eval suites as a GovChat-Eval dataset (independent audit layer).

This repo's own harness (`evals/`) is white-box: its checks know about
`guards.py`, the corpus doc-ids, and the agency-scope rules. GovChat-Eval
(a separate project, the `govchat-eval` CLI) is a black-box audit — it sees
only questions, recorded answers, and ground truth, and applies its own
suites and judge. Running both is the point: a system graded only by its
author is weaker than one an independent tool also audits.

This module records the deployed assistant's answers (same pipeline, corpus,
and pinned model the Lambda serves) and writes them, with ground truth, as a
content-hashed GovChat-Eval `golden.jsonl`. The committed dataset is then
replayed by `govchat-eval run --target scripted`, which is byte-reproducible
and runs offline in CI — the recording is the one live step, mirroring the
family's eval-record → scripted pattern (civic-rag-starter-kit ADR-K8).

    uv run python -m evals.govchat_export            # live record → dataset
    uv run python -m evals.govchat_export --offline  # mock model (plumbing only)

Suite mapping (fare-policy-assistant → GovChat-Eval):
    groundedness  → groundedness   (claims entailed by retrieved passages)
    edge_cases    → accuracy       (required_facts → expected_facts)
    refusal       → refusal        (refuse_redirect → should_refuse)
    multilingual  → multilingual   (mirror_of → pair_id, English mirror = reference)
    freshness     → accuracy / refusal, by expected behavior
Determination phrases and PII become representational `forbidden_terms`.
Freshness has no native GovChat-Eval suite; those cases ride accuracy/refusal
and the gap is noted in docs/audits/methodology.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re

from assistant import config
from assistant.answer import answer_question
from assistant.ingest import load_chunks
from assistant.models import get_model
from assistant.retrieve import Retriever
from evals import provenance
from evals.runner import load_suites, validate_cases

OUT_DIR = config.REPO_ROOT / "evals" / "govchat"
DATASET_PATH = OUT_DIR / "golden.jsonl"
ADDED = "2026-06-16"  # date this dataset was recorded (kept out of per-run hashing)
MAX_SOURCES = 6

# Cases that are genuine jailbreak attempts the assistant must refuse outright,
# tagged so GovChat-Eval's adversarial suite exercises them. refuse-019 is
# deliberately excluded: it is a partial-compliance injection inside an
# in-scope question (answer the fare, ignore the appended instruction), which
# the representational forbidden_terms check covers — not a refuse-everything
# case the adversarial suite's binary resist/comply model fits.
ATTACK_CASES: dict[str, str] = {
    "refuse-009": "prompt-injection",
    "refuse-010": "prompt-injection",
    "ml-013": "prompt-injection",
}


def _literal_facts(required_facts: list[str], answer: str) -> list[str]:
    """Turn this repo's `required_facts` (literal or `re:` regex) into the literal
    strings GovChat-Eval's lexical accuracy judge expects.

    For a regex we use the substring it actually matched in the recorded answer,
    so a fact that this repo's own check found present stays present for the
    independent check too. When the pattern does not match (a genuine failure),
    we fall back to a readable literal derived from the pattern so GovChat-Eval
    flags the same gap rather than silently dropping it.
    """
    facts: list[str] = []
    for f in required_facts:
        if not f.startswith("re:"):
            facts.append(f)
            continue
        pattern = f[3:]
        m = re.search(pattern, answer, re.I)
        if m:
            facts.append(m.group(0))
            continue
        lit = pattern.split("|")[0]
        for a, b in (
            (r"\$", "$"),
            (r"\s?", " "),
            (r"\s", " "),
            (r"\.", "."),
            (r"\b", ""),
            (r"\\", ""),
        ):
            lit = lit.replace(a, b)
        lit = re.sub(r"[\[\](){}?+*^]", "", lit).strip()
        facts.append(lit)
    return facts


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_transcript(question: str, answer: str, source_labels: list[str], lang: str) -> str:
    """An accessible HTML rendering of one Q&A turn for GovChat-Eval's a11y suite.

    Declares language, uses a non-skipping heading order (h2 -> h3), plain text
    and lists only — no images, controls, or inline colors — so the structural
    checker has a clean transcript to verify.
    """
    body = answer.replace("[doc:", "[").replace("]", "]")
    paras = "".join(f"<p>{_esc(p.strip())}</p>" for p in body.split("\n") if p.strip())
    sources = ""
    if source_labels:
        items = "".join(f"<li>{_esc(s)}</li>" for s in source_labels)
        sources = f"<h3>Sources</h3><ul>{items}</ul>"
    return (
        f'<section lang="{lang}"><h2>Rider question</h2><p>{_esc(question)}</p>'
        f"<h3>Answer</h3>{paras}{sources}</section>"
    )


def _provenance(result) -> dict[str, str]:
    agency = (
        result.citations[0].agency
        if result.citations
        else (
            result.passages[0].chunk.agency_full
            if result.passages
            else "fare-policy-assistant corpus"
        )
    )
    return {
        "source": f"{agency} published fare pages (see corpus/manifest.yaml)",
        "license": "public record — California transit agency fare policy pages",
        "added": ADDED,
        "note": "answer recorded from the deployed assistant pipeline",
    }


def _item_for(case: dict, result) -> dict:
    lang = case.get("language", "en")
    refused = result.kind != "answered"
    item: dict = {
        "id": case["id"],
        "question": case["question"],
        "provenance": _provenance(result),
        "target_response": {
            "text": result.answer,
            "refused": refused,
            "language": lang,
            # doc-ids the answer cited, as plain strings (ChatResponse.citations
            # is list[str]); the corpus passages themselves are the `sources`.
            "citations": [c.doc_id for c in result.citations],
        },
        # refuse_redirect is the only behavior that should refuse; answer and
        # partial should both produce an answer (over-refusal is a failure).
        "should_refuse": case["expected_behavior"] == "refuse_redirect",
        # An accessible rendering of this turn for the a11y suite.
        "transcript_html": render_transcript(
            case["question"],
            result.answer,
            [f"{c.agency}: {c.title}" for c in result.citations],
            lang,
        ),
    }

    # Groundedness only for English answered cases: the deterministic judge is
    # lexical, so scoring a Spanish answer against English passages would
    # manufacture failures that say more about the judge than the answer.
    if not refused and lang == "en" and result.passages:
        item["sources"] = [sc.chunk.text for sc in result.passages[:MAX_SOURCES]]

    facts = _literal_facts(case.get("required_facts", []), result.answer)
    if facts:
        item["expected_facts"] = facts

    forbidden = case.get("forbidden_content", [])
    if forbidden:
        item["forbidden_terms"] = list(forbidden)

    if case["id"] in ATTACK_CASES:
        item["attack"] = ATTACK_CASES[case["id"]]

    return item


def _pair_multilingual(items: dict[str, dict], cases_by_id: dict[str, dict]) -> None:
    """Wire `mirror_of` into GovChat-Eval's pair_id / is_reference grouping.

    Each Spanish case and its English mirror share a pair_id; the English mirror
    is the reference anchor. The multilingual suite then checks that the Spanish
    answer preserves the reference's figures and program names.

    Only factual mirrors are paired. A Spanish case that mirrors a refusal
    (injection, PII, out-of-corpus) has no figures or program names to preserve,
    so anchor fidelity is undefined there — pairing it would add a degenerate
    "0 anchors" item. Those cases are still audited by the refusal and
    adversarial suites.
    """
    for cid, case in cases_by_id.items():
        mirror = case.get("mirror_of")
        if not mirror or mirror not in items:
            continue
        if cases_by_id.get(mirror, {}).get("expected_behavior") == "refuse_redirect":
            continue
        pair_id = f"pair-{mirror}"
        items[cid]["pair_id"] = pair_id
        items[cid]["language"] = case.get("language", "es")
        ref = items[mirror]
        ref["pair_id"] = pair_id
        ref["is_reference"] = True
        ref.setdefault("language", cases_by_id[mirror].get("language", "en"))


def build_dataset(*, offline: bool = False) -> list[dict]:
    cfg = config.Config()
    if offline:
        cfg = config.Config(
            models=config.ModelConfig(provider="mock", answer_model="mock", judge_model="mock")
        )
    suites = load_suites()
    validate_cases(suites)

    chunks = load_chunks()
    retriever = Retriever(chunks, cfg.retrieval)
    model = get_model(cfg.models.provider, cfg.models.answer_model)

    items: dict[str, dict] = {}
    cases_by_id: dict[str, dict] = {}
    for s in suites:
        for case in s["cases"]:
            # The GovChat-Eval dataset schema is single-turn (one question, one
            # recorded answer); multi-turn conversation cases are exercised by
            # this repo's own runner, not exported to the black-box audit.
            if "question" not in case:
                continue
            result = answer_question(case["question"], model=model, retriever=retriever, cfg=cfg)
            items[case["id"]] = _item_for(case, result)
            cases_by_id[case["id"]] = case
            print(f"recorded {case['id']:<12} kind={result.kind}")

    _pair_multilingual(items, cases_by_id)
    # Preserve suite/case order from the loaded YAML for a stable diff.
    return [items[cid] for cid in cases_by_id]


def write_dataset(items: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Dataset-level provenance so the CI gate (evals/provenance.py) can verify
    # this audit dataset was recorded against HEAD's answer prompts and corpus.
    # Emitted as a `#` comment line the external govchat-eval reader skips.
    prov = provenance.provenance_block(ADDED, provenance.ANSWER_PROMPTS)
    prov_line = "# provenance: " + json.dumps(prov, ensure_ascii=False, sort_keys=True)
    header = (
        "# fare-policy-assistant — GovChat-Eval golden dataset (independent audit).\n"
        "# Generated by `python -m evals.govchat_export`; do not edit by hand.\n"
        "# Answers recorded from the deployed assistant; ground truth from the\n"
        "# eval suites under evals/suites/. See docs/audits/methodology.md.\n"
        f"{prov_line}\n"
    )
    lines = [header.rstrip("\n")]
    lines.extend(json.dumps(it, ensure_ascii=False) for it in items)
    DATASET_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    digest = hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()
    DATASET_PATH.with_suffix(".jsonl.sha256").write_text(digest + "\n", encoding="utf-8")
    print(f"\nwrote {len(items)} items → {DATASET_PATH}")
    print(f"sha256 → {digest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="mock model (plumbing only)")
    args = parser.parse_args()
    write_dataset(build_dataset(offline=args.offline))


if __name__ == "__main__":
    main()
