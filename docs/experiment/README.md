# AgentTrace Phase 1 Experiment Contract

This directory freezes the benchmark and measurement methodology before agent implementation or experimental results exist.

- [Experimental methodology](experimental-methodology.md) defines units, controls, held-out evaluation, run order, missing-data handling, and planned comparisons.
- [Benchmark task format](task-format.md) defines field semantics and path/test visibility rules.
- [JSON Schema](task.schema.json) is the machine-readable benchmark-task contract.
- [Corpus lock schema](corpus-lock.schema.json) binds each frozen task to its exact context, evaluator, baseline, environment, and policies without adding those experimental controls to task identity.
- [Metrics](metrics.md) fixes primary, secondary, and diagnostic measurements and their denominators.
- [Failure taxonomy](failure-taxonomy.md) fixes run-level labels and assignment rules.
- [Task selection](task-selection.md) defines benchmark admission and exclusion criteria.
- [Pilot-task concepts](pilot-task-concepts.md) supplies candidates for later fixture construction; they are not admitted benchmark tasks.

These documents refine the [Phase 0 project charter](../project-charter.md) without implementing the agent, native verifier, database, API, or interface.

## Freeze and change control

This Phase 1 contract is frozen as version `1.0` on 2026-08-20, before any AgentTrace experimental result was observed. The two JSON Schemas' `$id` values identify benchmark-task format version 1 and corpus-lock format version 1.

After data collection begins, a change to task eligibility, schema semantics, hidden-test visibility, outcomes, denominators, failure-label rules, configuration limits, or planned comparisons requires:

1. a new protocol version;
2. a written reason and date;
3. revalidation of affected tasks; and
4. separately identified results that are not pooled silently with version 1.

Typographical corrections that do not change meaning may retain the version but must still be recorded in version control once the repository begins using commits.
