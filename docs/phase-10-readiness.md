# Phase 10 research-readiness record

This record distinguishes completed backend validation from evidence that the
current Windows host cannot produce. It must not be cited as a completed main
experiment freeze until every item in **Freeze blockers** is cleared.

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

Qualification evidence is persisted beneath `.agenttrace/phase10-main`.
Every task has `baseline_status=verified`. Mutation fields are deliberately
null/incomplete with the reason `mutmut 3.7 requires OS fork support; use Linux
Docker or WSL`; zero mutants are not presented as a mutation score.

Ochiai localization completed for all 15 tasks. Known-fault containment is
9/15 at Top-1, 15/15 at Top-5, and 15/15 at Top-10. Coverage artifacts contain
repository-source observations and opaque hidden-test identifiers only.

## Integration and security validation

The common experiment interface was exercised for A, B, C, D, D1, D2, and D3
with the deterministic provider and an injected verification seam. The test
checks common result fields, feature separation, stable raw exports, provider
and token metadata, P0/P1 preservation, exactly one repair, counterexample
persistence, and complete attempt-2 verification. The real D3 benchmark
profile resolves CrossHair as enabled and builds a bounded CrossHair/Z3 plan;
actual symbolic execution remains a Docker-runtime check.

Phase 10 hardened dotenv and credential protection, JUnit evidence integrity,
isolated Python startup, property-result bounds, artifact permissions, and
Docker resource restrictions. Candidate patches continue to execute only in
Docker. Curator qualification and SBFL execute only fixed, trusted benchmark
fixtures; this curator trust boundary is distinct from candidate execution.
Docker reduces risk but is not a formal security guarantee. A writable output
bind has per-file and time limits but no aggregate filesystem quota.

## Reproducibility

Host dependency versions are constrained in
`constraints/main-experiment.txt`; the verifier image uses exact versions in
`docker/verification/requirements.txt`. A fresh virtual environment installed
the constrained development and qualification extras and passed 34 targeted
API, benchmark, and seven-condition integration tests. A clean state directory
then initialized the database, loaded and baseline-qualified the original
pilot tasks, generated SBFL, executed the 12-cell fake-provider matrix,
exported canonical version-2 traces, and resumed without changing raw hashes.
Docker absence was recorded as infrastructure failure rather than model
failure.

The selected provider identity is now part of the typed model configuration,
stable run digest, and fairness contract. The active adapter must match it.
Credentials remain process-external and are rejected from configuration and
trace metadata.

## Freeze blockers

The main experiment is **not frozen** on this host because:

1. Docker and the immutable verifier image ID are unavailable.
2. WSL/Linux is unavailable, so mutmut results for all 15 tasks are incomplete.
3. No real provider factory, model identifier, or immutable model version was
   selected in the specification or environment.

`experiments/main.example.yaml` records the intended 60-cell A/B/C/D matrix and
all other proposed settings, but its explicit provider/model placeholders make
it a template, not a frozen experimental condition. It must be copied to
`experiments/main.yaml`, completed, mutation-qualified, verified against a
pinned Docker image, and committed before Phase 11 data collection.
