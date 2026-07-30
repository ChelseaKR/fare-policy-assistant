# 0018 — Immutable Lambda release control

Date: 2026-07-30. Status: accepted; logging gate amended by ADR 0019;
reproducible bundle amendment accepted 2026-07-30.

## Context

ADR 0004 chose one Lambda behind an API Gateway HTTP API for the public demo.
The original deploy script updated the unqualified function directly. API
Gateway therefore invoked `$LATEST`, so configuration and code updates became
public before the script could verify the complete release. Recovery depended
on downloading the old zip and reconstructing mutable configuration.

The live AWS shape at this decision had one API Gateway-managed integration,
one `$default` route with automatic deployment, and no Lambda aliases. Three
old numbered versions existed, but they predated the current corpus pin,
disabled-document containment, and history-signing configuration. They were
not safe rollback targets.

AWS numbered Lambda versions snapshot code and most configuration. An alias is
a stable ARN that can move between numbered versions. Alias updates accept a
revision identifier, which provides an optimistic-concurrency guard. API
Gateway-managed quick-create integrations may be updated even though they
cannot be deleted.

## Decision

The public HTTP API invokes the qualified `live` Lambda alias. Routine
deployments never point API Gateway at `$LATEST` and never rewrite the
integration.

Each release follows this sequence:

1. Build the reviewed artifact from a clean Git revision. The ZIP builder sorts
   paths and normalizes timestamps, modes, and ZIP metadata so the same file
   bytes produce the same Lambda `CodeSha256`.
2. Stage complete code and configuration on `$LATEST`.
3. Publish or identify an exact numbered version using both the code hash and
   Lambda revision identifier.
4. Set that version's runtime update mode to `FunctionUpdate`.
5. Directly invoke the numeric version with API Gateway payload-v2 fixtures.
   The checks cover the root page, corpus pin and disabled-document state, PII
   refusal, one paid cited MST answer, and Yolobus containment when
   `yolobus-fares` is among the required disabled documents. ADR 0019 adds a
   fail-closed check of the paid answer's actual privacy-safe JSON model/answer
   records and the installed metric-filter grammar.
6. Point the `rollback` alias at the current `live` version.
7. Move `live` to the candidate with the alias revision identifier.
8. Run the public assistant smoke. If it fails, move `live` back immediately
   and fail the deployment.

Weighted routing is outside this release model. Both aliases must have an empty
`AdditionalVersionWeights` map before a release starts; every create, promotion,
rollback, and automatic restore explicitly writes an empty map and verifies the
response. A weighted alias therefore fails closed instead of leaking a
candidate into rider traffic.

The immutable `live` version is also the reviewed baseline for versioned
configuration the script does not own, including layers, VPC attachment, DLQ,
tracing, KMS, EFS, ephemeral storage, and SnapStart. Logging is intentionally
managed to the exact ADR 0019 JSON/INFO/WARN contract. Before staging,
the script compares mutable `$LATEST` with that baseline and refuses any
unmanaged drift. It repeats the comparison using the same full configuration
response whose revision identifier guards the update, and verifies the staged
candidate again before publication. Unknown future configuration fields remain
in this comparison and require an explicit code review before deployment.
The deploy also computes the local ZIP's AWS-style SHA-256 and requires both
the staging response and the later `$LATEST` read to match that digest, the
complete intended environment, and every managed runtime/configuration value.
The initial `live` version and alias revision remain the release-wide baseline;
a concurrent promotion causes this deployment to abort instead of combining
the old environment with the newer alias target.

The prepared dependency tree contains installation-time mtimes, and ordinary
`zip` preserves those values. That previously caused an unchanged Git revision
to publish a new numbered Lambda version when it was rebuilt during a rollback
drill. `scripts/build_lambda_zip.py` now includes only regular files, rejects
symlinks and other special entries, sorts POSIX archive names, and writes a
fixed timestamp and mode. Python bytecode caches and wheel `RECORD` files stay
excluded. Dependency-generated top-level `bin/` entry points are also excluded:
the Lambda handler does not use them, and their installer-written shebangs
contain the builder's absolute virtual-environment path. Exact-version reuse
can therefore depend on the artifact digest rather than incidental builder
filesystem metadata.

`rollback` retains the prior known-good numeric version. `infra/rollback.sh`
validates that version directly, checks its runtime mode and containment
state, proves that exactly one API integration targets the qualified `live`
alias, moves `live` with a revision guard, and runs the public smoke. If the
rollback target fails publicly, the script restores the displaced live
version. It verifies the integration again before accepting the rollback. The
recovery objective is under 15 minutes. One absolute operation deadline bounds
AWS calls, direct checks, curl retries, and final verification. Public
verification receives an earlier deadline so up to 60 seconds remains for the
revision-guarded restore if verification times out.

Promotion and rollback arm `EXIT`, `INT`, and `TERM` guards before moving
`live`. Until public smoke and final alias/integration checks succeed, abnormal
termination attempts a revision-guarded restore. Cleanup re-reads the alias and
will not overwrite a concurrent change. A failed promotion also restores the
previous `rollback` pointer with the same compare-and-swap discipline.

The current unqualified deployment requires a one-time migration. Before any
new code, Lambda configuration, IAM policy, concurrency, or gateway setting is
changed, the deploy script publishes the exact current `$LATEST`, verifies it,
creates both aliases, grants API Gateway permission on `live`, and updates the
managed integration. The old unqualified permission remains in place until
the qualified route passes its public smoke. Full configuration and revision
checks immediately before and after cutover prove that the unqualified source
did not change during migration; a failed check restores the original
integration without removing its permission.

Newly published versions use `FunctionUpdate` runtime management. This keeps
the managed Python runtime patch version with the tested release instead of
allowing it to change later under `Auto`. The project must deploy regularly to
pick up Lambda runtime security updates.

## Shared infrastructure boundary

Alias rollback covers Lambda code and versioned configuration, including the
corpus pin, disabled-document list, history-signing key, handler, runtime,
architecture, role ARN, memory, and timeout.

The execution role's inline policy, function reserved concurrency, API Gateway
stage throttle, alarms, dashboard, and log settings are shared mutable
infrastructure. An alias move cannot restore them. Routine deployment may
reconcile byte-equivalent shared settings. A changed IAM policy is refused
unless the operator explicitly sets `FPA_ALLOW_SHARED_IAM_CHANGE=1`; that
separate migration captures the prior policy, smokes the current live alias,
and restores the policy if the smoke fails.

The operator console must not claim that an unqualified
`UpdateFunctionConfiguration` call changes production. Published versions
cannot be mutated. Operator changes need a staged approval and promotion
workflow or must remain disabled.

## Consequences

- Half-applied code or environment changes on `$LATEST` are not rider-visible.
- Unreviewed unmanaged configuration on `$LATEST` blocks release rather than
  contaminating a later candidate.
- Promotion and rollback each require one small alias control-plane update.
- The stable public URL and API Gateway integration do not change per release.
- A failed candidate remains as an inspectable numeric version without
  receiving traffic.
- Previous client history signatures may stop validating after rollback
  because the history key is versioned. Dropping that history is safe.
- Rollback versions can become substantively unsafe as fare policy ages.
  Technical availability is not enough; direct health also verifies the
  approved pin and required disabled-document containment.
- Direct candidate checks share the function's reserved concurrency with live
  traffic. They run sequentially and should be scheduled away from known demo
  traffic.
- Published versions consume Lambda code storage. Never delete the versions
  targeted by `live` or `rollback`; add a conservative retention process only
  when storage usage warrants it.
- Rebuilding identical bundle inputs with the same compression implementation
  is byte-reproducible. Dependency wheel bytes remain protected separately by
  the hash-pinned requirements file.

## Rejected alternatives

- **Continue deploying to `$LATEST`.** It cannot provide a pre-public health
  gate or atomic code-and-configuration rollback.
- **Download-only rollback artifacts.** They are useful as a break-glass copy
  but require slower, error-prone reconstruction. The deploy still keeps one,
  sourced from the actual alias target.
- **Weighted alias canary.** Reserved concurrency is two and normal traffic is
  sparse. A percentage split would provide weak signal while adding routing
  and rollback states. Direct candidate invocation plus an atomic alias shift
  is more legible for this deployment.
- **Update API Gateway for every release.** The integration is shared mutable
  infrastructure. A permanent alias target makes routine release and rollback
  smaller.
- **Use `Auto` runtime updates.** AWS recommends it for many workloads, but it
  means a supposedly retained rollback version may receive a different
  runtime patch without this release gate. `FunctionUpdate` is accepted with
  the corresponding patch-deployment responsibility.

## References

- [Lambda function versions](https://docs.aws.amazon.com/lambda/latest/dg/configuration-versions.html)
- [Lambda aliases and resource policies](https://docs.aws.amazon.com/lambda/latest/dg/using-aliases.html)
- [Lambda runtime rollback](https://docs.aws.amazon.com/lambda/latest/dg/runtime-management-rollback.html)
- [API Gateway integration updates](https://docs.aws.amazon.com/cli/latest/reference/apigatewayv2/update-integration.html)
