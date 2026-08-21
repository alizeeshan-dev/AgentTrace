# Phase 3 benchmark qualification

Phase 3 defines an evaluator-owned YAML format and qualifies small repair tasks
before they can enter an AgentTrace experiment. The frozen Phase 1 portable
schema remains unchanged; the Phase 3 loader explicitly maps current
`task_category` manifests to the runtime `Task` entity.

## Corpus layout

- `benchmark/tasks/` contains versioned YAML manifests.
- `benchmark/repositories/` contains immutable one-commit Git bundles.
- `benchmark/patches/` contains known-correct unified diffs.
- `benchmark/hidden_tests/<task_id>/` is evaluator-owned and physically outside
  every agent-readable repository fixture.

The three pilot tasks cover empty-input handling, a business-rule boundary,
and a small text transformation. They are research fixtures, not the full
benchmark.

## Qualification workflow

For one manifest, the service loads and validates every corpus reference,
clones the fixed commit into a disposable workspace, runs visible and hidden
baseline tests, requires the declared bug to reproduce, applies and verifies
the known-correct patch, resets the clone, and then invokes `mutmut` once on the
corrected green test suite. Mutation testing is benchmark qualification only;
it is not part of per-patch agent verification.

Mutation evidence records the pinned tool version, exact argv, generated
configuration hash, platform and Python version, timestamps, bounded command
output, raw exported statistics, explicit mutant classifications, and the
score `killed / (killed + survived)`. Invalid, skipped, manually excluded, and
unusable mutants are recorded separately and never silently counted as
survivors.

The source bundle is never changed. Qualification logs, patch bytes, mutation
evidence, and the `BenchmarkQuality` snapshot are stored through the Phase 2
content-addressed artifact store. Repository, task, and quality rows are
upserted into SQLite only after the test gates and cleanup complete.

## Execution boundary

`mutmut==3.7.0` requires operating-system fork support, so mutation
qualification must run in Linux Docker or WSL. On an unsupported host, the
service still verifies the deterministic pre/post-patch gates and persists an
explicit `mutation_unavailable` result; it does not fabricate a score. The
curator-only local runner executes only trusted manifest commands. Arbitrary
agent patches remain deferred to the later network-denied Docker verifier.
