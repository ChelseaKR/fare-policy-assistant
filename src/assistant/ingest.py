"""Corpus ingestion: fetch → clean → chunk → index.

Usage:
    python -m assistant.ingest fetch     # snapshot manifest URLs into corpus/raw/
    python -m assistant.ingest process   # clean + chunk snapshots into corpus/processed/

Fetching is manifest-driven and polite: identified user agent, one pass, a
crawl delay between requests to the same host. Snapshots are committed so the
corpus a given eval run saw is always reconstructable.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml
from bs4 import BeautifulSoup, Tag

from assistant import config
from assistant import facts as facts_module

# Page furniture to drop wholesale during cleaning.
_STRIP_TAGS = ("script", "style", "nav", "header", "footer", "form", "noscript", "iframe", "svg")
_HEADING_TAGS = ("h1", "h2", "h3", "h4")
# Sections whose heading matches are navigation/boilerplate, not policy.
_BOILERPLATE_HEADINGS = re.compile(
    r"(quick links|follow us|newsletter|sign up|related pages|search|menu|share this)", re.I
)


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    agency: str
    agency_full: str
    doc_title: str
    url: str
    fetch_date: str
    language: str
    section: str
    text: str


def load_manifest() -> dict:
    return yaml.safe_load(config.MANIFEST_PATH.read_text(encoding="utf-8"))


# ── fetch ────────────────────────────────────────────────────────────────────


def fetch_all(only: set[str] | None = None) -> None:
    manifest = load_manifest()
    ua = manifest["user_agent"]
    delay = manifest.get("crawl_delay_seconds", 10)
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)

    last_hit: dict[str, float] = {}
    failures = []
    with httpx.Client(headers={"User-Agent": ua}, follow_redirects=True, timeout=30) as client:
        for doc in manifest["documents"]:
            if only and doc["id"] not in only:
                continue
            host = urlparse(doc["url"]).netloc
            wait = delay - (time.monotonic() - last_hit.get(host, -delay))
            if wait > 0:
                time.sleep(wait)
            last_hit[host] = time.monotonic()
            try:
                resp = client.get(doc["url"])
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                failures.append((doc["id"], str(exc)))
                print(f"FAIL  {doc['id']}: {exc}", file=sys.stderr)
                continue
            # Trust an explicit manifest `format: pdf`, or sniff the response
            # content type, so a PDF policy is snapshotted as .pdf and the
            # processor reads it through the PDF path (ADR 0008).
            is_pdf = (
                doc.get("format") == "pdf"
                or "application/pdf" in resp.headers.get("content-type", "").lower()
            )
            raw_path = config.RAW_DIR / f"{doc['id']}.{'pdf' if is_pdf else 'html'}"
            raw_path.write_bytes(resp.content)
            meta = {
                "doc_id": doc["id"],
                "url": doc["url"],
                "final_url": str(resp.url),
                "fetch_date": datetime.now(UTC).date().isoformat(),
                "http_status": resp.status_code,
                "format": "pdf" if is_pdf else "html",
                "sha256": hashlib.sha256(resp.content).hexdigest(),
                "bytes": len(resp.content),
            }
            (config.RAW_DIR / f"{doc['id']}.meta.yaml").write_text(
                yaml.safe_dump(meta, sort_keys=False), encoding="utf-8"
            )
            print(f"ok    {doc['id']}  {len(resp.content):>8} bytes  {resp.url}")

    if failures:
        print(f"\n{len(failures)} document(s) failed; manifest entries unchanged.", file=sys.stderr)
        raise SystemExit(1)


# ── clean + chunk ────────────────────────────────────────────────────────────


def clean_html(html: str) -> Tag:
    soup = BeautifulSoup(html, "html.parser")
    for tag_name in _STRIP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    assert isinstance(main, Tag)
    return main


def _node_text(node) -> str:
    text = node.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)


def _looks_transposed(a: str, b: str) -> bool:
    """True when two adjacent pipe rows are a transposed label/value table.

    A transposed table stores parallel labels and their values as two
    equal-width rows aligned only by column index (e.g. a row of pass names
    over a row of their conditions). Fare *data* rows are excluded: they carry
    figures (digits), and a header row rarely matches a data row's width.
    """
    fa = [x.strip() for x in a.split("|")]
    fb = [x.strip() for x in b.split("|")]
    if len(fa) != len(fb) or len(fa) < 3:
        return False
    if not all(fa) or not all(fb):
        return False
    return not any(ch.isdigit() for ch in a + b)


def normalize_tables(body: str) -> str:
    """Append explicit ``label: value`` lines for transposed pipe tables.

    A transposed table aligns labels and values by column index only, which the
    model mis-reads (eval case edge-025: the UC Davis pass conditions). The
    original lines are kept so retrieval tokens are unchanged; the appended
    aligned pairs give the model a form it can read directly. Fires only on
    genuinely transposed, digit-free tables (see `_looks_transposed`), so normal
    fare tables are left untouched.
    """
    lines = body.split("\n")
    extra: list[str] = []
    for a, b in zip(lines, lines[1:], strict=False):
        if "|" in a and "|" in b and _looks_transposed(a, b):
            fa = [x.strip() for x in a.split("|")]
            fb = [x.strip() for x in b.split("|")]
            extra.extend(f"{x}: {y}" for x, y in zip(fa, fb, strict=False))
    return body + "\n" + "\n".join(extra) if extra else body


def sections_from_html(html: str) -> list[tuple[str, str]]:
    """Split a page into (heading, text) sections, one per policy section.

    Walks the cleaned DOM in order; a new section starts at each heading tag.
    Tables are linearized row by row so fare amounts stay attached to their labels.
    """
    main = clean_html(html)
    sections: list[tuple[str, list[str]]] = [("(page top)", [])]
    for el in main.descendants:
        if not isinstance(el, Tag):
            continue
        if el.name in _HEADING_TAGS:
            heading = _node_text(el)
            if heading:
                sections.append((heading, []))
        elif el.name == "tr":
            cells = [_node_text(c) for c in el.find_all(["td", "th"])]
            row = " | ".join(c for c in cells if c)
            if row:
                sections[-1][1].append(row)
        elif el.name in ("p", "li"):
            if el.find_parent("table"):
                continue
            text = _node_text(el)
            if text:
                sections[-1][1].append(text)

    return _finalize_sections(sections)


def _finalize_sections(sections: list[tuple[str, list[str]]]) -> list[tuple[str, str]]:
    """Shared tail for HTML and PDF section extraction: drop boilerplate, dedupe
    repeated lines, normalize transposed tables, and fold tiny fragments into the
    preceding section so every chunk carries enough tokens to be retrieved."""
    out: list[tuple[str, str]] = []
    for heading, parts in sections:
        if _BOILERPLATE_HEADINGS.search(heading):
            continue
        # Dedupe lines (nav menus repeat) while preserving order.
        seen: set[str] = set()
        lines = []
        for p in parts:
            if p not in seen:
                seen.add(p)
                lines.append(p)
        body = normalize_tables("\n".join(lines).strip())
        if len(body) < 40:
            continue
        # Tiny sections are usually address blocks or table fragments split
        # off from the policy text they belong to; standalone they carry too
        # few word tokens to ever be retrieved (eval case edge-017). Fold
        # them into the preceding section, keeping their heading inline.
        if len(body) < 200 and out:
            prev_heading, prev_body = out[-1]
            out[-1] = (prev_heading, f"{prev_body}\n{heading}\n{body}")
        else:
            out.append((heading, body))
    return out


# ── PDF ingest (optional; see docs/decisions/0008) ───────────────────────────

# A heading line in extracted PDF text: short, starts with a capital or digit,
# not a sentence (no terminal punctuation), a phrase not a paragraph.
_PDF_HEADING = re.compile(r"^[A-Z0-9].{0,78}$")


def _looks_like_heading(line: str) -> bool:
    if not (2 <= len(line) <= 80) or line[-1] in ".!?,:;":
        return False
    return bool(_PDF_HEADING.match(line)) and len(line.split()) <= 9


def sections_from_text(text: str) -> list[tuple[str, str]]:
    """Split flat PDF-extracted text into (heading, body) sections.

    A PDF has no heading tags, so headings are inferred: a short, capitalized,
    sentence-less line starts a new section; everything else is body. Falls back
    to a single "(document start)" section when no headings are found. The shared
    finalize step then dedupes, normalizes tables, and merges tiny fragments, so a
    PDF chunk is shaped like an HTML one and cites the same way.
    """
    sections: list[tuple[str, list[str]]] = [("(document start)", [])]
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        if _looks_like_heading(line):
            sections.append((line, []))
        else:
            sections[-1][1].append(line)
    return _finalize_sections(sections)


def extract_pdf_text(data: bytes, *, ocr: bool = False) -> str:
    """Extract text from a PDF.

    Default path reads the embedded text layer with pypdf (pure Python, no system
    binaries). `ocr=True` rasterizes and runs OCR for scanned PDFs that carry no
    text layer; that path needs the optional OCR extras and the tesseract and
    poppler system binaries, so it is not exercised in CI. See ADR 0008 for the
    tradeoffs and when each path applies.
    """
    if ocr:
        return _ocr_pdf_text(data)
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        raise ModuleNotFoundError(
            "PDF ingest needs the 'pdf' extra: `uv pip install '.[pdf]'`."
        ) from exc
    import io

    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    return "\n\n".join(p for p in pages if p)


def _ocr_pdf_text(data: bytes) -> str:  # pragma: no cover - needs system binaries
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "OCR fallback needs the 'ocr' extra (pytesseract, pdf2image) plus the "
            "tesseract and poppler system binaries. See docs/decisions/0008."
        ) from exc
    pages = convert_from_bytes(data)
    return "\n\n".join(pytesseract.image_to_string(img).strip() for img in pages)


def process_all() -> None:
    manifest = load_manifest()
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    all_chunks: list[Chunk] = []

    for doc in manifest["documents"]:
        fmt = doc.get("format", "html")
        raw_path = config.RAW_DIR / f"{doc['id']}.{'pdf' if fmt == 'pdf' else 'html'}"
        meta_path = config.RAW_DIR / f"{doc['id']}.meta.yaml"
        if not raw_path.exists():
            print(f"skip  {doc['id']} (no snapshot; run `make fetch`)", file=sys.stderr)
            continue
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        if fmt == "pdf":
            sections = sections_from_text(
                extract_pdf_text(raw_path.read_bytes(), ocr=doc.get("ocr", False))
            )
        else:
            sections = sections_from_html(raw_path.read_text(encoding="utf-8", errors="replace"))

        md_lines = [
            f"# {doc['title']} — {doc['agency']}",
            f"Source: {doc['url']} (fetched {meta['fetch_date']})",
            "",
        ]
        for i, (heading, body) in enumerate(sections):
            chunk = Chunk(
                chunk_id=f"{doc['id']}#{i}",
                doc_id=doc["id"],
                agency=doc["agency"],
                agency_full=doc["agency_full"],
                doc_title=doc["title"],
                url=doc["url"],
                fetch_date=meta["fetch_date"],
                language=doc.get("language", "en"),
                section=heading,
                text=body,
            )
            all_chunks.append(chunk)
            md_lines += [f"## {heading}", "", body, ""]
        (config.PROCESSED_DIR / f"{doc['id']}.md").write_text("\n".join(md_lines), encoding="utf-8")
        print(f"ok    {doc['id']}: {len(sections)} sections")

    with config.CHUNKS_PATH.open("w", encoding="utf-8") as f:
        for chunk in all_chunks:
            f.write(json.dumps(asdict(chunk), ensure_ascii=False) + "\n")
    print(f"\nwrote {len(all_chunks)} chunks → {config.CHUNKS_PATH}")

    build_facts()

    # Local import: assistant.corpus imports Chunk/load_manifest/load_chunks from
    # this module, so importing it at module scope here would be circular.
    # Retain this content under corpus/versions/<id>/ (EXP-05) so a past eval
    # run's exact corpus stays loadable by version id after a later re-ingest.
    from assistant.corpus import archive_version

    version = archive_version(all_chunks, manifest)
    print(f"archived corpus version {version} → {config.VERSIONS_DIR / version}")


def build_facts() -> None:
    """Derive the FareFact table (EXP-01) from the chunks just written.

    Automated extraction (`confidence="parsed"`) is fully re-derived every
    run; any hand-curated `confidence="manual"` rows already committed at
    `facts.jsonl` are preserved across the rebuild. See `assistant.facts`.
    """
    chunks = load_chunks()
    parsed = facts_module.build_facts(chunks)
    all_facts = facts_module.merge_manual_rows(parsed, config.FACTS_PATH)
    facts_module.write_facts(all_facts, config.FACTS_PATH)
    manual_count = sum(1 for f in all_facts if f.confidence == "manual")
    print(f"wrote {len(all_facts)} fare facts ({manual_count} manual) → {config.FACTS_PATH}")


def load_chunks(path: Path | None = None) -> list[Chunk]:
    path = path or config.CHUNKS_PATH
    chunks = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            chunks.append(Chunk(**json.loads(line)))
    return chunks


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "process"
    if cmd == "fetch":
        fetch_all(only=set(sys.argv[2:]) or None)
    elif cmd == "process":
        process_all()
    else:
        raise SystemExit(f"unknown command: {cmd} (expected fetch|process)")


if __name__ == "__main__":
    main()
