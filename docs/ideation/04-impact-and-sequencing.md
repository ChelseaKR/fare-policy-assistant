# Impact × effort and sequencing

Drafted 2026-07-01. Covers FIX-01…FIX-12 (`02-large-scale-fixes.md`) and
EXP-01…EXP-14 (`03-expansions.md`). "Impact" here means impact on the
repo's actual thesis — trustworthy, honest evaluation of a civic assistant —
not feature volume. This sequence goes beyond `docs/ROADMAP.md` and the
RR/RE roadmap; where an existing item is a prerequisite it is named, not
re-planned.

## Impact × effort matrix

| | **S** | **M** | **L** | **XL** |
|---|---|---|---|---|
| **High impact** | FIX-03 | FIX-01 · FIX-02 · FIX-05 · FIX-09 · FIX-12* | EXP-01 · FIX-07 | EXP-04 · EXP-12 · EXP-14 |
| **Medium impact** | EXP-03 | FIX-04* · FIX-06 · FIX-08 · FIX-11 · EXP-02 · EXP-07 · EXP-10 | EXP-05 · EXP-06 · EXP-13* · EXP-09 | EXP-11 |
| **Lower / situational** | | FIX-10 | EXP-08 | |

\* = the build is one tier, but *proving* it needs live credentials (see
gates below). FIX-12 is rated high not for what it does but for what it
unblocks: it makes the live-validation discipline affordable, which is the
binding constraint on everything judge-scored.

Notes on placement, honestly argued:

- **FIX-01, FIX-02, FIX-03 are high-impact because they defend the
  headline.** Each is a way the published numbers can currently drift from
  the truth without anyone noticing (stale artifacts, judge blindness,
  stale labels). For this repo specifically, a small integrity bug outranks
  a large feature.
- **FIX-10 (CSP) is real but lower-ranked**: the demo has no secrets and no
  rider data; it matters most the moment an agency embeds the widget, so it
  should precede any embed promotion, not everything else.
- **EXP-08 (SMS) is deliberately parked** at lower/situational despite real
  equity upside, because its privacy design gate is unresolved and the
  no-PII posture is a hard rule, not a preference.

## Dependency notes

- **FIX-12 → FIX-04 → (EXP-02 judge half, EXP-14).** Cheap runs enable
  replicates; replicates give variance; only a variance-aware harness can
  responsibly A/B prompts or define conformance thresholds.
- **FIX-02 + FIX-03 → RR6 (existing).** Fix judge context and label binding
  *before* growing the calibration sample, or the new labels inherit both
  defects. These three should land as one campaign with the deferred
  calibrated-judge regeneration.
- **EXP-01 → EXP-04, EXP-06, EXP-07.** The fact table is the substrate for
  the answer contract, the GTFS cross-check, and the no-model guide.
- **FIX-07 and FIX-09 precede corpus growth** (EXP-10, EXP-12): the decline
  threshold must survive IDF shift and the freshness loop must be
  closed before the corpus multiplies.
- **FIX-06 precedes EXP-11** (second domain live) — late binding is exactly
  what a real fork will trip over.
- **FIX-01 precedes the pending branch merge** in spirit: merging
  `research-panel-and-roadmap` (prompts v7/v4) without the provenance gate
  reproduces the exact staleness the gate exists to catch. If the merge
  comes first, the required live `make eval` + baseline update + audit
  re-record is the manual equivalent.

## Suggested sequence (beyond the existing roadmaps)

**Now (integrity campaign — mostly offline-buildable):**
1. FIX-03 (label binding) and FIX-02's code half (judge context) — small,
   surgical, and they must exist before the next live run so that run's
   calibration is clean.
2. FIX-01 (provenance gate) — land it, then do the pending branch merge +
   live `make eval` regen under its protection (the regen itself is
   credential-gated; see below).
3. FIX-12 (runner caching/parallelism) — the enabler for everything
   judge-scored that follows.
4. FIX-09 (freshness loop) and FIX-05 (guard language parity) — both
   self-contained, both close observed gaps in already-shipped promises.
5. EXP-03 (eval-history page) — hours of work, permanent credibility
   artifact.

**Next (measurement maturity + core deepening):**
6. FIX-04 (variance/replicates) once FIX-12 makes it affordable; publish
   intervals in `EVALS.md`.
7. FIX-06 (late-bind profile), FIX-08 (forged-history cases + optional
   HMAC), FIX-11 (language ID) — three medium fixes that each unblock a
   documented deferred item (adapting promise, RE4 frontier, RE1/Tagalog).
8. EXP-01 (fare-fact layer), then EXP-02 (sensitivity suite) and EXP-07
   (no-model guide) on top of it.
9. FIX-07 (calibrated decline) with its ablation, before any corpus growth.
10. FIX-10 (CSP) before promoting `/embed` to any real agency.

**Later (bets, in rough order of readiness):**
11. EXP-05 (longitudinal corpus) and EXP-06 (GTFS cross-check — start with
    the feed survey).
12. EXP-10 (agency kit) → then real agency #6; EXP-09 (operator console) if
    an actual agency conversation materializes to shape it.
13. EXP-13 (local-model comparison) as a published experiment; EXP-11
    (second-domain proof) coordinated with civic-rag-starter-kit.
14. EXP-04 (answer contract) — highest-value H1 bet, but only after FIX-04
    exists to measure its regression risk honestly.
15. EXP-12 (statewide commons) and EXP-14 (conformance program) — only on
    top of a calibrated, variance-aware, provenance-gated harness.

## Items requiring human / legal / SME / real-data / credential gates

Per the portfolio ethos: these are deferred and reported, never faked. No
item below should be marked done on the strength of offline work.

**Credential-gated (live Bedrock/Anthropic access):**
- The pending **live `make eval` regen** after the v7/v4 prompt merge, plus
  `evals/baseline.json` update and GovChat dataset re-record (existing
  deferred item; FIX-01 makes its absence visible instead of silent).
- The **calibrated LLM-judge audit regeneration** (`--judge llm` GovChat run;
  existing deferred RR6 companion).
- FIX-02/FIX-04 validation runs; the judge-scored halves of EXP-02 and
  FIX-08's forged-history suite.

**Human-gated:**
- Manual screen-reader/keyboard walkthrough (existing RR7 /
  `docs/audits/a11y-walkthrough.md` — still the gate for any
  "production-ready" claim, including EXP-04's re-rendered answers and
  EXP-07's guide pages).
- FIX-03/RR6 relabeling: human judgment on every calibration row, ideally
  two labelers so inter-annotator agreement can be reported alongside κ.

**Legal / counsel-gated:**
- **Title VI / LEP one-pager counsel review** (existing deferred RR8): the
  artifact can be drafted, but presenting it as compliance evidence without
  a counsel read would be exactly the overclaim this repo refuses.
- EXP-08 (SMS): privacy design review before any build — phone numbers are
  PII by transport.
- EXP-14: certification-adjacent language ("conforms," registry claims)
  needs counsel comfort before anything public.

**SME / partner-gated:**
- EXP-09 and EXP-10 want a real agency operator's workflow to shape them;
  building the console on guessed workflows would waste the effort.
- EXP-14 thresholds need transit-accessibility, Title VI, and eval-research
  SME review; EXP-11's benefits-domain will-not-do list needs a domain SME.
- EXP-13's kiosk half needs a venue partner; the backend comparison itself
  does not.

**Real-data-gated:**
- EXP-06 starts with an unverified premise (which agencies publish usable
  GTFS fares data) — the survey is step zero and its result should be
  committed whichever way it comes out.
- Everything the synthetic panel "found" remains hypothesis until real
  riders are observed (the validation plan in
  `docs/research/synthetic-personas-feedback.md` still stands; nothing in
  this folder substitutes for it).
