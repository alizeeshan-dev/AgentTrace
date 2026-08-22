# Phase 10 research-readiness record

This record distinguishes the completed Phase 10 engineering candidate from
the native-Windows migration and empirical evidence that still must be frozen.
It must not be cited as a completed main-experiment freeze until every item in
**Freeze blockers** is cleared.

## Benchmark inventory

Benchmark version candidate: `benchmark-v1.0.0`.

| Task | Class | Difficulty | Property | Symbolic | True-fault rank |
| --- | --- | --- | --- | --- | ---: |
| algorithm-contained-interval-merge | algorithmic | hard | no | no | 1 |
| algorithm-rightmost-binary-search | algorithmic | medium | no | no | 1 |
| api-cache-expiry-boundary | API/application | medium | no | no | 2 |
| api-json-content-type-parameters | API/application | medium | no | no | 1 |
| api-pagination-one-based | API/application | easy | no | no | 1 |
| boundary-empty-input | boundary/empty input | easy | Hypothesis | no | 1 |
| boundary-window-last | boundary/off-by-one | easy | Hypothesis | no | 1 |
| parsing-csv-quoted-field | parsing | hard | no | no | 1 |
| parsing-query-equals | parsing | medium | no | no | 3 |
| transformation-deep-merge | transformation | hard | no | no | 2 |
| transformation-flatten-order | transformation | easy | no | no | 3 |
| transformation-slug-collapse | transformation | easy | no | no | 2 |
| validation-business-rule | validation/business | easy | no | no | 1 |
| validation-capacity-equality | validation/business | easy | no | no | 2 |
| validation-discount-threshold | validation/business | easy | no | PEP 316 | 1 |

The corpus contains eight easy, four medium, and three hard tasks. For all 15,
the fixed bundle commit was checked out, the visible baseline passed, the
external hidden suite reproduced the defect, the known-correct patch applied,
and the corrected visible and hidden suites passed. The 31 invariant tests in
`backend/tests/test_benchmark_tasks.py` repeat these checks.

## Qualification and SBFL evidence

Legacy qualification evidence is persisted beneath `.agenttrace/phase10-main`.
Every task has `baseline_status=verified`. The legacy mutation fields are
deliberately null/incomplete and must be replaced by real pytest-gremlins
qualification results before the benchmark is frozen; zero gremlins must not
be presented as a mutation score unless the tool completed and actually
generated none.

Ochiai localization completed for all 15 tasks. Known-fault containment is
9/15 at Top-1, 15/15 at Top-5, and 15/15 at Top-10. Coverage artifacts contain
repository-source observations and opaque hidden-test identifiers only.

## Integration and security validation

The common experiment interface was exercised for A, B, C, D, D1, D2, and D3
with the deterministic provider and an injected verification seam before the
native-Windows migration. That evidence covers common result fields, feature
separation, stable raw exports, provider and token metadata, P0/P1
preservation, exactly one repair, counterexample persistence, and complete
attempt-2 verification. Native CrossHair/Z3 execution remains subject to the
separate migration QA.

The current architecture executes only trusted, controlled, pre-qualified
benchmark repositories. Candidate and qualification commands run in disposable
Git workspaces through the restricted Windows subprocess runner, using an
isolated Python virtual environment where required, explicit working
directories, sanitized environments, bounded output, process-tree termination,
and hard timeouts. Path, hidden-test, `.git`, patch, artifact, and credential
protections remain part of the boundary. Native Windows subprocess isolation
is weaker than VM/container isolation and is not a production-grade sandbox
for arbitrary untrusted code.

## Reproducibility

Host dependency versions are constrained in
`constraints/main-experiment.txt`. The frozen experiment must record a Windows
environment manifest containing the OS and Python versions, verification-tool
versions (including pytest-gremlins), dependency-lock hash, source commit,
benchmark version, verification profile, and stable environment fingerprint.
This fingerprint replaces the former container-image identity in experiment
metadata.

The selected provider identity is now part of the typed model configuration,
stable run digest, and fairness contract. The active adapter must match it.
Credentials remain process-external and are rejected from configuration and
trace metadata.

## Freeze blockers

The main experiment is **not frozen** because:

1. The separate migration QA must validate the native Windows verifier and
   complete pytest-gremlins qualification for all 15 tasks.
2. The frozen Windows environment manifest and fingerprint must be generated
   from the validated environment.
3. No real provider factory, model identifier, or immutable model version was
   selected in the specification or environment.
4. `experiments/main.yaml` does not yet contain finalized scientific values.

The native Windows verifier and pytest-gremlins qualification pipeline are the
only current experiment environment; no secondary compatibility environment is
a freeze prerequisite.

`experiments/main.example.yaml` records the intended 60-cell A/B/C/D matrix and
all other proposed settings, but its explicit provider/model placeholders make
it a template, not a frozen experimental condition. After migration QA, it
must be copied to `experiments/main.yaml`, completed with the frozen Windows
environment fingerprint and real provider/model settings, linked to persisted
pytest-gremlins qualification evidence, and committed before Phase 11 data
collection.
