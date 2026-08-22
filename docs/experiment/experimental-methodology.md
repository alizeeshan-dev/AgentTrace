# Experimental Methodology

## 1. Objective

The experiment estimates the incremental effects of controlled repository inspection and one deterministic-verification-assisted repair opportunity on LLM-generated Python patches. It implements the A/B/C contrasts and H1–H4 defined in the [project charter](../project-charter.md); it is not a contest to maximize an unconstrained agent's solve rate.

## 2. Units and analysis sets

- **Task:** one admitted benchmark manifest at one immutable repository commit with fixed baseline outcomes and evaluation tests.
- **Run:** one task × configuration execution beginning with its first model request and ending at its declared terminal state.
- **Block:** the A, B, and C runs for the same task under the same model snapshot and parameter set.
- **Valid run:** an evaluated run not excluded as evidenced local infrastructure or model-provider missing data under the charter's recovery rule.
- **Primary analysis set:** all valid runs from every frozen main-benchmark task. Model refusals, malformed responses, policy-rejected patches, tool-limit exhaustion, patch failures, and patch-caused timeouts remain valid unresolved runs.

Every task has equal weight in aggregate rates. Pilot tasks used to debug later engineering are excluded from confirmatory results; their outcomes must be labelled `pilot` and reported separately.

## 3. Experimental conditions

The normative definitions of Direct Patch (A), Tool Agent (B), and Verified Agent (C) are in charter sections 5.2–5.4. For every block:

- A, B, and C receive the same task statement and recorded prepared initial context.
- They use the same model snapshot, initial-response parameters, patch format, final evaluation, and resource policy.
- B and C receive the same read-only tools, tool behavior, pre-patch tool-step limit, and tool-output limits.
- A and B receive no verification result before their terminal patch response.
- C receives at most one repair request, only under the charter's deterministic trigger, and cannot call repository tools after feedback.
- No human edits a patch before automated scoring.

The global generated-patch policy permits at most five changed files and at most 200 added-plus-removed lines across all patch targets. It is identical across tasks and configurations. A candidate exceeding either bound fails deterministic patch policy as `EXCESSIVE_CHANGE`; the limit is not inferred from the reference patch or changed after viewing a candidate.

The primary study uses one run per task/configuration (`pass@1`). A/B model calls are never retried, and C's sole repair is the intervention rather than a retry. Repeated stochastic model runs are an optional, separately labelled study and cannot be pooled into the primary analysis.

## 4. Visible verification versus held-out evaluation

Phase 1 fixes an ambiguity left open by the generic phrase “required verification check” in Phase 0: final hidden tests are evaluation-only and never become repair feedback. This preserves a genuinely held-out outcome without changing the A/B/C attempt limits.

### 4.1 Agent-visible gates

The following may trigger C's repair and may appear in its bounded structured feedback:

- unified-diff validation and patch policy;
- patch application;
- the visible test command;
- configured lint, type, and static-analysis checks; and
- timeouts attributable to the patch in those gates.

Only check identifiers, status, exit codes, bounded diagnostics, and relevant output may be returned. Hidden test source, hidden command text, hidden test identifiers, hidden output, expected patches, and human repair instructions are never returned or made inspectable.

### 4.2 Hidden evaluator

After the model interaction ends, the evaluator runs every configured final check independently where doing so is safe; a visible, lint, type, or static-analysis failure does not conceal a hidden or regression outcome. It runs the hidden command against each applied final patch and derives two fixed sets from baseline qualification:

- **FAIL_TO_PASS:** task-specific tests with the predeclared failing baseline outcome that must pass after a correct patch.
- **PASS_TO_PASS:** regression tests that pass on the baseline and must remain passing.

All tests in both sets must pass for primary task resolution. The same test identities and command are used for A, B, and C. Visible, lint, type, and static-analysis outcomes remain required secondary measurements but do not alter TRR unless their checks are also part of the frozen hidden inventory. Consequently, a TRR-resolved run can have a failing secondary quality check; it receives no primary failure-taxonomy label, and the check failure remains visible in metrics and review evidence.

For the within-C initial-versus-final analysis, the captured initial patch is evaluated later in a fresh working copy. This offline measurement is not shown to the model, cannot trigger repair, and is excluded from agent end-to-end latency; its evaluator time is recorded separately.

## 5. Task and corpus freeze

Before main runs, every task must pass the [admission procedure](task-selection.md), validate against [task schema version 1](task.schema.json), and appear in a [corpus lock](corpus-lock.schema.json) that binds it to immutable hashes for its manifest, prepared context, hidden evaluator, baseline outcome inventory, dependency/environment definition, tool policy, and verification profile. The complete main task list and corpus lock are then frozen.

The main benchmark target is at least 15 admitted tasks, consistent with the MVP roadmap. If fewer than 15 task blocks remain after predeclared missing-data exclusions, results are reported as a pilot/descriptive study and no confirmatory claim is made.

Tasks must not be selected or removed based on agent outcomes. Post-freeze removal is permitted only for a documented admission defect—such as a flaky baseline, inaccessible repository, leaked hidden test, or invalid oracle—and applies to all configurations for that task.

## 6. Execution order and controls

1. Record the model identifier/version, provider, parameters, context limit, tool limits, feedback limits, verifier profile, dependency lock information, and AgentTrace revision.
2. Create the task blocks before any main run.
3. Generate and store a pseudorandom A/B/C order within each block using one predeclared randomization seed.
4. Interleave blocks rather than completing one configuration globally first, reducing temporal provider or machine-load bias.
5. Use clean disposable working copies for every patch attempt and final evaluation.
6. Run checks through the same restricted Windows subprocess policy for every configuration: explicit disposable-workspace working directory, sanitized environment, bounded output, isolated virtual environment where required, and hard per-command timeout.
7. Preserve all terminal failures and deviations; never manually substitute a better response.

If a model snapshot or a material provider behavior changes before all blocks finish, stop the experiment. Resume as a new protocol/model cohort rather than pooling incomparable runs.

## 7. Missing data and recovery

- A local harness/environment failure is labelled `INFRASTRUCTURE_FAILURE`.
- An external model transport, service outage, or rate-limit failure with no usable model response is labelled `MODEL_PROVIDER_FAILURE`.
- Both are missing data only when evidence shows the failure was outside the model's generated behavior and proposed patch.
- A provider refusal, malformed response, unsafe proposal, or terminal model-caused tool error/tool-limit exhaustion is a valid unresolved run, not missing data. A recoverable invalid tool request remains a diagnostic event if the run later produces a final patch.
- Before the first model request, a failed planned run may restart once.
- After an artifact is captured, only the failed non-model stage may recover once from that artifact. A completed model action is never repeated.
- Failed recovery remains missing data, is disclosed, and is not imputed.

Task-level A/B/C comparisons use complete valid pairs for the relevant contrast. Counts and reasons for missing runs are always reported by configuration and task.

## 8. Planned comparisons

| Hypothesis | Frozen contrast | Outcome |
| --- | --- | --- |
| H1 | B minus A | Task Resolution Rate |
| H2a | C final minus B final | Task Resolution Rate |
| H2b | C final minus C initial | Paired task resolution |
| H3 | C minus B | Overall regression-producing patch rate |
| H4a | C minus B | End-to-end latency |
| H4b | C minus B | Total tokens (`input_tokens + output_tokens`) |

For binary paired outcomes, report both configurations' counts, the percentage-point paired risk difference, a 95% paired bootstrap confidence interval, and a two-sided exact McNemar test. For latency, tokens, patch size, and tool calls, report the median, interquartile range, paired median difference, and a paired bootstrap interval. H4a and H4b use a two-sided exact paired sign test; the predicted direction remains C greater than B. If either token component is unavailable, H4b is missing for that pair and no alternate usage value is substituted. Cost is descriptive because pricing can change.

Effect sizes and uncertainty are primary. The six confirmatory tests H1, H2a, H2b, H3, H4a, and H4b form one Holm-corrected family with familywise α = 0.05. Conditional regression rate is a required sensitivity measure but has no confirmatory p-value; input/output tokens separately, cost, and analyses outside the table are descriptive or exploratory. No threshold converts deterministic test passing into proof of semantic correctness.

## 9. Blinding and annotation

Automated outcomes are computed without human adjustment. Failure-taxonomy annotation occurs from frozen traces after terminal status is known. When a causal secondary label requires judgment, one annotator records evidence and a second reviews it without seeing aggregate configuration results; disagreements are resolved and documented before rates by configuration are calculated.

Human patch acceptance is an oversight outcome, not the primary task-resolution oracle. Reviewers must not alter automated outcomes based on patch style or preference.

## 10. Reporting minimum

The report must include:

- protocol and schema versions;
- the frozen task list, task difficulties, and exclusions;
- model and harness controls;
- numerator, denominator, missing-data count, and uncertainty for every rate;
- paired task-level A/B/C outcomes;
- initial and final C outcomes;
- latency, tokens, price-snapshot cost, and repair/tool usage;
- primary and secondary failure labels with evidence summaries;
- all protocol deviations; and
- limitations, including test-oracle incompleteness, small Python-only scope, model stochasticity, provider drift, and limited statistical power.
