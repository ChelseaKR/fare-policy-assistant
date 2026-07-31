# 0021 — Release identity and deployment attestation

Date: 2026-07-30. Status: accepted for the rider-service rollout.

## Context

ADR 0020 added behavior-complete web-policy content and source-snapshot
identities while preserving the original 12-character `corpus_version` as a
compatibility pin. That still leaves several ways for materially different
systems to report the same public identity:

- prompt bodies can change without their first-line version labels changing;
- model, retrieval, domain, containment, history, freshness, and presentation
  settings are not represented by the corpus;
- a warm answer cache and signed conversation history are bound only to the
  legacy corpus and disabled-document set;
- a Git revision, Lambda ZIP, versioned Lambda configuration, numeric Lambda
  version, and `/version` response are checked independently rather than as one
  release tuple; and
- the evaluation `--since` cache can reuse a case across configuration or
  behavior-complete content changes.

The deployment bundle cannot contain the digest of the final ZIP that contains
it. A timestamp or Lambda numeric version also cannot be an input to a
reproducible application identity. Older Lambda versions do not contain the new
descriptor, so the first rollout must retain one explicitly identified
legacy-only rollback target without weakening new-candidate checks.

## Decision

Add two schema-framed, canonical JSON identities:

- `config_version` uses schema `fare-assistant.config.v1`. It covers the
  resolved model and retrieval configuration, exact bytes of all four prompts,
  selected domain-profile behavior, source containment, staleness and embed
  policy, request/history/cache limits, answer-contract bytes, judge-call
  settings, and whether history signing is enabled. A deterministic,
  domain-separated key ID covers history-key rotation; the secret itself is
  never serialized or returned.
- `release_version` uses schema `fare-assistant.release.v1`. It covers the full
  clean 40-character source revision, `config_version`, and a sorted evidence
  list. The first evidence scope is `web_policy`, carrying full
  `content_version` and `snapshot_version` values.

Hashes are full lowercase SHA-256 values over
`schema-ascii + NUL + canonical-json-utf8`, with sorted object keys, compact
separators, and non-finite numbers rejected.

The deterministic bundle contains `release/release.json`. It has no creation
time, Lambda number, alias, deployment-placement region, or ZIP digest. The
behavior-affecting model transport region or endpoint is configuration and is
therefore included. The descriptor records:

- descriptor, config, and release schemas;
- full source revision and `source_state: "clean"`;
- the canonical public configuration payload and its digest;
- the scoped web-policy content and snapshot identities;
- the release digest; and
- the legacy `corpus_version` compatibility value.

Before building the ZIP, deployment validates the complete schema-2 snapshot
archive and requires its chunks to equal the chunks being bundled. Dirty
identity-bearing production deployment is prohibited. The deployer copies the
same identity tuple into versioned Lambda environment variables. After the ZIP
is built, its AWS-style `CodeSha256` is added as deployment metadata, outside
`release_version`.

At runtime, a numeric Lambda version requires a descriptor and the complete
identity environment. The service:

1. strictly validates the descriptor;
2. recomputes `config_version` from the bundled prompts, contract, resolved
   environment, and code-owned configuration;
3. recomputes legacy and full content identities from the bundled chunks;
4. recomputes `release_version`;
5. compares every result with the descriptor and environment; and
6. reports `identity_status: "verified"` only when all agree.

The source snapshot identity cannot be recomputed without shipping all raw
evidence in the rider Lambda. It is instead validated before bundling, committed
to the descriptor, frozen in the Lambda environment, and compared at candidate
health. The public response identifies the deployment realization separately
with the ZIP digest and numeric function version.

`/version` and answer envelopes add the full source, configuration, content,
snapshot, and release identities while retaining `corpus_version`. Answer-cache
keys and signed history bind to `release_version`, so neither can cross a
release boundary in a warm process.

A numbered candidate is promotable only if all of these agree:

- locally generated descriptor;
- locally built ZIP digest;
- settled and published Lambda configuration;
- exact numeric direct invocation;
- `/version` identity tuple and numeric function version; and
- the identity-bearing alias description.

The ZIP digest is verified as deployment evidence but is not folded into
`release_version`, avoiding a recursive artifact hash. Exact Lambda-version
reuse continues to compare the full code and versioned AWS configuration.

## Compatibility rollout

New candidates always require the complete identity tuple. A missing or partial
tuple is never accepted for a new release.

For the first identity-bearing release only, deployment and rollback may use an
explicitly observed, numeric legacy baseline as the retained rollback target.
That exception is scoped to that exact version and an explicit legacy health
mode. Once rollback points to an identity-capable version, the exception is
removed; it is not a general “fields optional” path. Historical releases are
labelled legacy rather than assigned identities they never carried.

Evaluation artifacts and the operator console migrate in the following slice.
A committed evaluation report cannot truthfully include the final commit's
source-based release identity because committing that report changes the source
revision. Exact-candidate evaluations therefore become post-commit CI/promotion
receipts; committed baselines remain comparison inputs.

Transactional GTFS and GTFS Scorecard evidence remain separate. Once exact GTFS
ZIP capture exists, a future release schema can add a second scoped evidence
entry without treating Scorecard as runtime or policy truth.

## Consequences

- A prompt-body, model, retrieval, domain, containment, history-key, or other
  represented runtime-policy change produces a new `config_version`.
- A source, runtime configuration, policy-content, or source-observation change
  produces a new `release_version`.
- Operators can trace an answer from its envelope to a logical release, an
  immutable Lambda number, and an exact deployment ZIP.
- Reverification can preserve content identity while changing snapshot and
  release identity.
- Key rotation invalidates prior signed history and changes the release without
  exposing the signing key.
- The descriptor is reproducible for a clean commit and effective environment;
  redeploying the same code and configuration can reuse the exact numbered
  Lambda version.
- Provider model IDs do not prove immutable upstream weights. Served-model
  telemetry and repeated evaluation remain necessary operational evidence.

## Rejected alternatives

- **Use only the Git revision.** Runtime environment and source snapshots can
  change behavior independently of source code.
- **Use only the ZIP digest.** It is a deployment artifact identity, not a
  readable or composable application release contract.
- **Put the ZIP digest inside the descriptor.** The digest would recursively
  depend on itself.
- **Hash prompt headers.** A body edit can retain the same human label.
- **Record only that history signing is enabled.** Key rotation changes which
  prior turns are accepted.
- **Serialize or return the history secret.** A public opaque key ID provides
  the needed rotation boundary without exposing key material.
- **Infer identities for old versions.** Evidence not captured at publication
  time cannot be reconstructed honestly.
- **Fold GTFS Scorecard into this schema now.** The current GTFS fetch path does
  not retain an exact transactional ZIP and Scorecard is advisory evidence, not
  rider-facing fare-policy authority.
