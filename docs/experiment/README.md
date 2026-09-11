# AgentTrace Experiment Contract

This directory defines the benchmark and measurement methodology independently of implementation and experimental outcomes.

- [Experimental methodology](experimental-methodology.md) defines units, controls, held-out evaluation, run order, missing-data handling, and planned comparisons.
- [Benchmark task format](task-format.md) defines field semantics and path/test visibility rules.
- [JSON Schema](task.schema.json) is the machine-readable benchmark-task contract.
- [Corpus lock schema](corpus-lock.schema.json) binds each frozen task to its exact context, evaluator, baseline, environment, and policies without adding those experimental controls to task identity.
- [Metrics](metrics.md) fixes primary, secondary, and diagnostic measurements and their denominators.
- [Failure taxonomy](failure-taxonomy.md) fixes run-level labels and assignment rules.
- [Task selection](task-selection.md) defines benchmark admission and exclusion criteria.

These documents refine the [project charter](../project-charter.md) and define the research protocol used by the implementation.

## Freeze and change control

This contract is frozen as version `1.0` on 2026-08-20, before any AgentTrace experimental result was observed. The two JSON Schemas' `$id` values identify benchmark-task format version 1 and corpus-lock format version 1.

After data collection begins, a change to task eligibility, schema semantics, hidden-test visibility, outcomes, denominators, failure-label rules, configuration limits, or planned comparisons requires:

1. a new protocol version;
2. a written reason and date;
3. revalidation of affected tasks; and
4. separately identified results that are not pooled silently with version 1.

Typographical corrections that do not change meaning may retain the version but must still be recorded in version control once the repository begins using commits.
