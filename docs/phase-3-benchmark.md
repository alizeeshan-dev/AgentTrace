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
the known-correct patch, resets the clone, and then invokes `pytest-gremlins`
once on the corrected green test suite. Mutation testing is benchmark qualification only;
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

Mutation qualification runs natively on Windows through the
pytest-gremlins adapter. The adapter preserves the normalized AgentTrace
statistics and records unavailable, excluded, skipped, or problematic
gremlins explicitly rather than presenting them as survivors. Qualification
and later patch verification execute only evaluator-controlled commands from
trusted, pre-qualified benchmark repositories in disposable Git workspaces,
using sanitized environments and hard subprocess timeouts. AgentTrace does not
execute arbitrary untrusted third-party repositories.
