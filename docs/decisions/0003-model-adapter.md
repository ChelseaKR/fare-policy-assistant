# ADR 0003: Provider-portable model adapter; Bedrock by default

Date: 2026-06-12 (amended same day: Bedrock promoted from stub to default).
Status: accepted.

## Decision

All model calls go through a small adapter (`Model.complete`) in
`src/assistant/models.py` with three backends:

- `bedrock` (default): Claude on Amazon Bedrock through the Anthropic SDK's
  Bedrock client. Model IDs carry the `anthropic.` provider prefix
  (`anthropic.claude-haiku-4-5` for answers, `anthropic.claude-sonnet-4-6`
  as judge), pinned in config. Credentials resolve through the standard AWS
  chain; region comes from `AWS_REGION`. This matches the builder's prior
  production deployments and what government clients typically require.
- `anthropic`: the direct Anthropic API, behind `FPA_PROVIDER=anthropic`,
  with the unprefixed model IDs. Useful when an Anthropic key is easier to
  obtain than AWS access.
- `mock`: deterministic, offline. Exercises prompt assembly, citation
  extraction, and guards in tests and credential-less CI runs. Mock results
  are labeled offline in every report and never presented as model quality.

The judge must differ from the answer model; the runner asserts it.

## Why an adapter at all

So that switching providers is a config change with an eval run attached,
not a rewrite. The eval harness is what makes the portability claim
testable: run the full suite on each backend and compare scoreboards.
