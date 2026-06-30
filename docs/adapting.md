# Adapting this harness to another domain

The harness generalizes to any assistant that answers questions from
published policy documents: benefits eligibility, licensing rules, housing
programs. This page lists what changes and what carries over unchanged.

## What carries over unchanged

- The runner, deterministic check framework, judge plumbing, report
  generator, regression gate, and CI wiring (`evals/`).
- The guard architecture: input checks before retrieval, output checks that
  block and substitute rather than merely log (`src/assistant/guards.py`).
- The corpus discipline: a manifest with URLs, fetch dates, hashes, and
  license notes; committed snapshots; "as of" disclosure in every answer.

## What you change

0. **The domain profile.** The transit-specific knobs are isolated in one
   object, `DomainProfile` in `src/assistant/domain.py`: the scopes (agencies),
   the aliases users type for them, the adjacent topics to redirect, and the
   fallback contact. A new domain writes a new profile and registers it; the
   retrieval, guard, and config code reads the active profile unchanged. The
   `test_a_new_domain_is_just_a_new_profile` case shows a housing-voucher profile
   reusing the whole pipeline. What is deliberately not in the profile, because
   it is cross-domain safety rather than domain content, is the PII, injection,
   and eligibility-determination detectors in `guards.py`; those bind in every
   domain.

1. **Corpus manifest.** Point `corpus/manifest.yaml` at your documents.
   Check robots.txt and content signals; record your reading of them in the
   manifest, not just in your head. Re-run `make fetch && make ingest`.

2. **The will-not-do list.** Decide what your assistant must never do (for
   a benefits assistant: determine eligibility, advise on appeals, handle
   case numbers). Encode each rule three times: in the system prompt, in
   `guards.py`, and as eval cases. The repetition is the design.

3. **Forbidden-language patterns.** The determination-language detector is a
   phrase list with hedge awareness. Rewrite the phrases for your domain
   ("you are approved", "your claim will succeed") in every language you
   serve.

4. **Eval cases.** Author cases from your actual documents, during or right
   after ingest, while the boundary conditions are in front of you. The
   pattern to copy from `evals/suites/`:
   - groundedness: facts a reader can verify against a named passage;
   - refusal: PII, injection, determination-seeking, out-of-corpus topics;
   - edge cases: the boundaries your documents actually publish (ages,
     income cutoffs, document alternatives, what stacks with what);
   - multilingual: mirrored cases so parity is a number, not a hope;
   - freshness: expired programs and "as of" behavior.

5. **Agency/entity aliases.** Set on the `DomainProfile` (item 0): whatever
   your users call the programs or offices in your corpus.

## The two habits that matter

Commit your first bad scoreboard. The improvement curve across commits is
evidence your evals connect to reality.

Change prompts only against failing cases. Every prompt edit should name the
case IDs it is trying to fix, and the regression gate catches what it broke.
