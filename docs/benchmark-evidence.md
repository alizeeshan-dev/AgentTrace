# Benchmark validation evidence

This document records the validated construction and fault-localization evidence
for the AgentTrace benchmark candidate. These values are benchmark engineering
results, not measurements of LLM patch quality.

## Inventory

Candidate benchmark version: `benchmark-v1.0.0`.

| Task | Class | Difficulty | Property profile | Symbolic profile | True-fault rank |
| --- | --- | --- | --- | --- | ---: |
| `algorithm-contained-interval-merge` | algorithmic | hard | no | no | 1 |
| `algorithm-rightmost-binary-search` | algorithmic | medium | no | no | 1 |
| `api-cache-expiry-boundary` | API/application | medium | no | no | 2 |
| `api-json-content-type-parameters` | API/application | medium | no | no | 1 |
| `api-pagination-one-based` | API/application | easy | no | no | 1 |
| `boundary-empty-input` | boundary/empty input | easy | Hypothesis | no | 1 |
| `boundary-window-last` | boundary/off-by-one | easy | Hypothesis | no | 1 |
| `parsing-csv-quoted-field` | parsing | hard | no | no | 1 |
| `parsing-query-equals` | parsing | medium | no | no | 3 |
| `transformation-deep-merge` | transformation | hard | no | no | 2 |
| `transformation-flatten-order` | transformation | easy | no | no | 3 |
| `transformation-slug-collapse` | transformation | easy | no | no | 2 |
| `validation-business-rule` | validation/business | easy | no | no | 1 |
| `validation-capacity-equality` | validation/business | easy | no | no | 2 |
| `validation-discount-threshold` | validation/business | easy | no | PEP 316 | 1 |

The corpus contains eight easy, four medium, and three hard tasks. For all 15
tasks, the fixed Git bundle commit was checked out, the visible baseline passed,
the evaluator-owned hidden suite reproduced the defect, the known-correct patch
applied, and the corrected visible and hidden suites passed. These invariants are
parameterized in `backend/tests/test_benchmark_tasks.py`.

## Fault-localization evidence

Ochiai localization was evaluated for all 15 tasks against the declared fault
location. Known-fault containment was:

| Cutoff | Containment |
| --- | ---: |
| Top-1 | 9/15 (60%) |
| Top-5 | 15/15 (100%) |
| Top-10 | 15/15 (100%) |

Coverage artifacts retain repository-source observations and opaque hidden-test
identifiers only. A high suspiciousness rank is execution evidence, not proof
that a line is faulty.

## Incomplete qualification evidence

The benchmark was migrated from mutmut to pytest-gremlins. Complete
pytest-gremlins qualification results have not yet been persisted for all 15
tasks, so no mutation score is reported. Existing incomplete or legacy mutation
fields must not be interpreted as zero-strength oracles.

The frozen Windows environment manifest, exact real-model snapshot, pricing,
and final `experiments/main.yaml` are also pending. The intended 60-cell main
matrix has not been executed; deterministic fake-provider runs are integration
fixtures rather than model-performance results.
