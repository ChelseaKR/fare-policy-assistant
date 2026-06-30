# ADR 0008: PDF policy ingest, text-first with an OCR fallback

Date: 2026-06-20. Status: accepted. Resolves open question #3 in CLAUDE.md.

## Decision

Ingest can read a PDF policy, not only HTML. A manifest document marked
`format: pdf` (or a fetch whose response content type is `application/pdf`) is
snapshotted as `corpus/raw/<id>.pdf` and processed through a PDF text path
instead of the HTML one. Extraction is text-layer first with pypdf (pure Python,
no system binaries), behind the optional `pdf` extra. An OCR fallback for scanned
PDFs with no text layer is implemented behind `ocr: true` on the document and the
`ocr` extra, but is not part of the default install or CI.

## Why

CLAUDE.md open question #3 flagged that some agencies publish fare policy as PDF,
and the corpus already paid for it: MST's Courtesy Card and military programs
moved to PDF application forms, so those criteria are currently uncitable
(`corpus/manifest.yaml` notes this). A retrieval assistant whose whole contract
is "every answer cites a dated source passage" cannot reach a policy it cannot
read. The capability closes that gap without changing the contract: a PDF chunk
carries the same fields, cites the same way, and is dated the same way as an HTML
chunk.

## How it works

- **Fetch.** `fetch_all` writes `.pdf` when the manifest says `format: pdf` or the
  server returns a PDF content type, and records `format` in the snapshot meta.
  Politeness, dating, and the sha256 are unchanged.
- **Extract.** `extract_pdf_text(bytes)` reads each page's text layer with pypdf
  and joins the pages. `ocr=True` routes to `_ocr_pdf_text`, which rasterizes with
  pdf2image and runs pytesseract; it raises a clear error if the extras or the
  tesseract/poppler binaries are missing.
- **Section.** A PDF has no heading tags, so `sections_from_text` infers them: a
  short, capitalized, sentence-less line starts a section; everything else is
  body. The result then runs through the same `_finalize_sections` tail as HTML
  (dedupe, transposed-table normalization, tiny-fragment merge), so PDF and HTML
  chunks are shaped identically and the rest of the pipeline is unchanged.

## Consequences

- Core install stays light. PDF support is the `pdf` extra (`pypdf`); OCR is the
  heavier `ocr` extra plus system binaries. The code lazy-imports both and fails
  with install guidance, so a missing dependency is a clear message, not a crash.
- Heading inference is a heuristic. PDF layout is messier than a DOM, so the
  section split is rougher than HTML; the finalize merge keeps that from
  producing un-retrievable fragments, and any specific PDF added to the corpus
  should have its chunks eyeballed before its eval cases are written.
- OCR is honest about its limits: it is wired and documented but not exercised in
  CI, because tesseract and poppler are system binaries the CI image does not
  carry. The text-first default covers the common case (born-digital PDFs with a
  text layer); OCR is there for scans when a real one shows up.
- No corpus document is PDF yet. This ADR ships the capability and its tests; the
  first agency PDF is a follow-up that fetches a real document, snapshots it, and
  writes its edge cases from the extracted text.

## Caveat

Text-layer extraction quality depends on how the PDF was produced. A cleanly
exported PDF extracts well; a complex multi-column or form-field layout can
interleave text, and a pure-image scan extracts nothing without OCR. The decision
is "read PDFs, text-first, OCR when needed and available," not "PDFs are as clean
as the HTML corpus."
