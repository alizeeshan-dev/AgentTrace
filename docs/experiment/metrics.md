# Metrics Specification

This specification is frozen before experimental runs. Metrics are calculated from final run artifacts unless a metric explicitly names Configuration C's initial patch.

## 1. Common statuses and populations

Every check records one of `PASS`, `FAIL`, `TIMEOUT`, `NOT_RUN`, or `NOT_CONFIGURED`. `NOT_RUN` is never treated as a pass. `NOT_CONFIGURED` is excluded only from metrics for optional verification profiles, with coverage counts reported.

- **Valid run:** an evaluated run that is not evidenced missing data under the infrastructure/model-provider rules in [experimental methodology](experimental-methodology.md).
- **Resolved valid run:** a valid run whose final patch applies, every required hidden FAIL_TO_PASS test passes, and every hidden PASS_TO_PASS regression test remains passing.
- **Configured valid run:** a valid run whose task/repository verification profile declares the relevant optional check.
- **Applied final patch:** a syntactically valid final unified diff that passes path policy and applies completely to a clean baseline.

Model refusals, missing or invalid diffs, patch-policy rejection, patch-application failures, tool failures attributable to the agent, and patch-caused timeouts remain in valid-run denominators and are unresolved.

Visible, lint, type, and static-analysis checks are required secondary measurements, not extra TRR conjuncts unless included in the frozen hidden inventory. A run may therefore resolve under TRR while failing a secondary quality check; that failure must be reported and shown to reviewers rather than changing the primary outcome after the fact.

## 2. Primary metric

### Task Resolution Rate

> **Task Resolution Rate (TRR) = 100 × resolved valid runs / all valid runs**

A run is resolved only when its final patch passes all required hidden task tests without regressions. Concretely, the patch must apply, all FAIL_TO_PASS tests must pass, and all PASS_TO_PASS tests must pass. The visible suite is a diagnostic/repair gate; hidden evaluation remains the scoring oracle.

Report TRR for A, B, and C with numerator, denominator, percentage, missing-data count, and a 95% task-level bootstrap confidence interval. Report the planned paired percentage-point differences B−A and C−B with paired bootstrap intervals. Do not rename pass@k or best-of-k results as TRR; the primary study is pass@1.

## 3. Secondary metrics

| Metric | Definition | Denominator / summary | Important handling |
| --- | --- | --- | --- |
| Final patch application rate | Final patch passes syntax, policy, and clean-baseline application. | All valid runs. | Refusal, missing diff, invalid diff, forbidden write, and failed hunk count as not applied. |
| Visible-test pass rate | Final patched state returns `PASS` for the complete visible command. | All valid runs. | `NOT_RUN` and timeout do not pass. Report baseline-visible status distribution too. |
| Hidden task-test pass rate | Every required FAIL_TO_PASS test passes on the final patched state. | All valid runs. | This may be true even when a regression makes TRR false. |
| Overall regression rate | At least one PASS_TO_PASS test has a patch-attributable non-pass final result. | All valid runs. | A missing/unapplied patch is unresolved but not regression-producing because no patched state exists. |
| Conditional regression rate | Same regression numerator as above. | Valid runs with an applied final patch. | Report beside overall rate so failure to produce patches cannot look artificially safe. |
| Lint pass rate | Configured lint check returns `PASS` on the final patch. | Configured valid runs. | Report configured-run coverage; `NOT_CONFIGURED` is not a pass. |
| Type-check pass rate | Configured type checker returns `PASS` on the final patch. | Configured valid runs. | Record tool/version and configured scope. |
| Static-analysis pass rate | No new configured static-analysis finding at or above the frozen failure threshold. | Configured valid runs. | Report new findings by severity and rule; do not claim absence of vulnerabilities. |
| Patch files changed | Count of distinct files in the applied final diff. | Distribution over applied final patches. | Report coverage and also the proportion violating frozen size policy. |
| Lines added | Added non-diff-metadata lines in the applied final diff. | Median, IQR, and distribution over applied final patches. | Generated/lockfile exclusions, if any, must be frozen before runs. |
| Lines removed | Removed non-diff-metadata lines in the applied final diff. | Median, IQR, and distribution over applied final patches. | Same counting implementation for all configurations. |
| Patch churn | `lines_added + lines_removed`. | Median and IQR over applied final patches. | Do not infer patch quality from size alone. |
| Tool-call count | Number of validated repository-tool requests issued by the model. | Per valid run; summarize B and C. | A is structurally zero. Invalid requests are counted separately and also included in attempted calls. |
| Repair-attempt count | Whether C consumed its one repair response (`0` or `1`). | All valid C runs. | Refusal, missing diff, or invalid repair response still consumes one attempt. A and B are structurally zero. |
| Repair-trigger rate | C initial responses that failed an eligible visible verification gate and caused the repair request. | All valid C runs. | Hidden evaluation cannot trigger repair. |
| Repair conversion rate | Initially unresolved C patches whose final repair becomes resolved under offline hidden evaluation. | Valid C runs that consumed repair and whose initial patch was evaluable. | Also report resolved-to-unresolved and regression-introducing repair transitions. |
| End-to-end latency | Wall time from first model request through terminal final verification. | Median, IQR, and paired difference per configuration. | Excludes offline C-initial hidden scoring; includes waits and the C repair path. |
| Model latency | Sum of model-request durations in the run. | Distribution per configuration. | Record provider-reported and locally measured timing when both exist. |
| Verification latency | Time spent in patch gates and configured checks during the run. | Distribution per configuration/check. | Offline analysis overhead is reported separately. |
| Input tokens | Provider-reported input tokens summed across model calls. | Median, IQR, total, and paired difference. | Record missing usage metadata; do not estimate silently. |
| Output tokens | Provider-reported output tokens summed across model calls. | Median, IQR, total, and paired difference. | Same tokenizer/provider accounting within a cohort. |
| Total tokens | `input_tokens + output_tokens` when both are known. | Median, IQR, total, and paired difference. | Unknown components make total unknown. |
| Estimated model cost | Sum of token usage × frozen price snapshot for the exact model/tier, plus declared provider charges if applicable. | Currency total and per-run distribution. | Store price source, currency, effective date, and formula; cost is descriptive and never backfilled without versioning. |

For Configuration C, retain initial-patch versions of application, visible, hidden task-test, regression, lint, typing, static-analysis, and patch-size metrics. Hidden initial-patch metrics are computed offline and are never agent feedback.

## 4. Diagnostic metrics

| Metric | Definition |
| --- | --- |
| Invalid tool-call rate | Invalid/rejected tool requests divided by all attempted tool requests for B and C. |
| Invalid patch rate | Valid runs whose final response does not contain one policy-valid unified diff divided by valid runs. |
| Patch-application failure rate | Valid runs with a policy-valid diff that fails clean-baseline application divided by valid runs. |
| Timeout rate | Valid runs with a model-, tool-, or patch-attributable timeout divided by valid runs, broken down by stage. |
| Hallucinated path/symbol rate | Valid runs with trace-supported `HALLUCINATED_PATH_OR_SYMBOL` divided by valid runs, with annotation coverage. |
| Excessive-change rate | Valid runs violating allowed/forbidden paths or the global maximum of five changed files or 200 added-plus-removed lines divided by valid runs. |
| Failure-label distribution | Count and percentage of primary and secondary taxonomy labels by configuration. |
| Verification-gate distribution | Count of each initial/final gate status by configuration and check. |

## 5. Aggregation and reporting rules

1. Preserve raw integer counts; percentages alone are insufficient.
2. Macro-average by run/task so large test suites do not give one task more weight.
3. Keep check-level counts available diagnostically, but do not substitute micro-averaged test cases for TRR.
4. Use complete task pairs for A/B and B/C contrasts and state pair counts.
5. Do not impute missing data or convert infrastructure/provider failures into model failures.
6. Report zero separately from unknown and `NOT_CONFIGURED`.
7. Keep successful repair transitions, failed repairs, and repair-introduced regressions visible.
8. Treat human acceptance as a separate oversight metric, not a replacement for hidden-test resolution.
