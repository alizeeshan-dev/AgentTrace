# Failure Taxonomy

## 1. Assignment contract

Every terminal unresolved or missing-data run receives exactly one **primary** label and zero or more **secondary** labels. “Unresolved” is defined by TRR: no applied final patch, a remaining FAIL_TO_PASS failure, or a PASS_TO_PASS regression. A TRR-resolved run receives no primary failure label, although a failing visible/lint/type/static secondary check may be recorded as a diagnostic secondary label. An initial C failure that is successfully repaired remains a trace-level repair-trigger event, not a terminal primary label.

Primary labels describe the terminal outcome using deterministic evidence whenever possible. Secondary labels add causal diagnoses or simultaneous failures. Every assigned label stores an evidence reference to the relevant trace step, patch hunk, check result, or curator note; unsupported speculation is not a label.

## 2. Labels

| Label | Use when | Evidence / boundary |
| --- | --- | --- |
| `MISUNDERSTOOD_REQUIREMENT` | The response or patch demonstrably implements behavior inconsistent with a clear task statement. | Cite the conflicting requirement and patch/response behavior. Do not use merely because a hidden test failed. Usually secondary to the terminal gate label. |
| `INSUFFICIENT_REPOSITORY_INSPECTION` | In B or C, available trace evidence shows the agent committed to a patch without inspecting a directly relevant location discoverable within its remaining tool budget. | Requires a named relevant file/symbol and trace review. Never assign to A, which has no iterative tools. |
| `HALLUCINATED_PATH_OR_SYMBOL` | The agent refers to or edits a path or symbol that does not exist at the pinned baseline and was not intentionally introduced by the patch. | Repository lookup plus tool/patch evidence. A typo causing a hunk failure may also receive `PATCH_DID_NOT_APPLY`. |
| `INVALID_PATCH` | The terminal response is missing a diff or contains a malformed, ambiguous, multi-patch, or policy-unparseable unified diff. | Patch parser/validator result. A model refusal or prose-only response falls here rather than provider failure. |
| `PATCH_DID_NOT_APPLY` | A syntactically and policy-valid unified diff cannot apply completely to a fresh clean baseline. | Patch-application result and rejected hunk. Do not use for forbidden paths or malformed syntax. |
| `VISIBLE_TEST_FAILURE` | The applied final patch produces a failing required visible test and no higher-priority terminal outcome applies. | Per-test/command status from the visible suite. |
| `HIDDEN_TEST_FAILURE` | One or more FAIL_TO_PASS tests remain non-passing after an applied final patch, without a higher-priority regression outcome. | Evaluator-only test inventory and final status. Hidden details remain outside the agent trace view. |
| `REGRESSION` | At least one baseline PASS_TO_PASS test becomes non-passing for a reason attributable to the applied patch. | Stable baseline inventory and final test status. This takes primary precedence over simultaneous task-test failure. |
| `LINT_FAILURE` | A configured lint or non-security static-analysis check fails on the final patched state. It is primary only for an unresolved run when no higher-priority outcome applies; on a TRR-resolved run it may be diagnostic secondary evidence. | Frozen tool/version/rules and finding output. |
| `TYPE_FAILURE` | The configured type-check gate fails on the final patched state and no higher-priority outcome applies. | Frozen type checker/version/scope and diagnostics. |
| `SECURITY_WARNING` | The patch introduces a new static-analysis finding classified as security-relevant at or above the frozen threshold. It is primary only for an unresolved run when no higher-priority outcome applies; otherwise it may be diagnostic secondary evidence. | Tool rule, severity, and baseline/final comparison. This is a warning label, not proof of exploitability or a production security claim. |
| `TIMEOUT` | A model, tool, or verification stage exceeds its declared limit for a reason attributable to the run or patch. | Stage timing and limit. A clean-baseline or harness timeout is infrastructure missing data instead. |
| `TOOL_MISUSE` | In B or C, an invalid, forbidden, or repeatedly inappropriate tool request prevents production of an evaluable final patch. | Validated tool request/result and terminal state. Invalid calls that do not determine the terminal outcome are secondary. |
| `EXCESSIVE_CHANGE` | A patch violates allowed/forbidden path policy, changes more than five files, or has more than 200 added-plus-removed lines. | Deterministic diff/path-policy result. Human acceptance and style judgments never assign this label or change automated resolution. |
| `INFRASTRUCTURE_FAILURE` | Local checkout, dependency, isolation, harness, storage, or evaluator failure makes the outcome unavailable and is reproducible independently of the proposed patch. | Baseline/control reproduction and infrastructure logs. It is missing data, not a valid unresolved model run. |
| `MODEL_PROVIDER_FAILURE` | External provider transport, outage, or rate-limit failure yields no usable model response after permitted recovery. | Provider/transport status and absence of a completed response. Refusal, bad model output, or invalid request constructed by AgentTrace is not provider failure. |

## 3. Primary-label decision order

Use this order when multiple labels are supported:

1. **Missing data:** `MODEL_PROVIDER_FAILURE` or `INFRASTRUCTURE_FAILURE`, selected by where the evidenced failure originated.
2. **No evaluable patch:** `TOOL_MISUSE` if tool behavior caused terminal exhaustion; otherwise `INVALID_PATCH`, `EXCESSIVE_CHANGE` for path/size policy rejection, or `PATCH_DID_NOT_APPLY`.
3. **Applied patch timeout:** `TIMEOUT` when a bounded stage cannot produce more specific per-check results.
4. **Behavioral regression:** `REGRESSION` if any PASS_TO_PASS regression fails.
5. **Unresolved target behavior:** `HIDDEN_TEST_FAILURE` when FAIL_TO_PASS checks remain non-passing. Use `VISIBLE_TEST_FAILURE` as primary only when a visible failure prevents hidden scoring from safely completing for a non-missing-data reason. If the complete hidden inventory passes, visible failure is diagnostic secondary evidence and the run remains TRR-resolved.
6. **Other recorded checks when hidden scoring could not safely complete:** `SECURITY_WARNING`, `TYPE_FAILURE`, then `LINT_FAILURE` according to the frozen check outcome.
7. **Causal diagnosis:** use `MISUNDERSTOOD_REQUIREMENT`, `INSUFFICIENT_REPOSITORY_INSPECTION`, or `HALLUCINATED_PATH_OR_SYMBOL` as primary only when no deterministic terminal category above applies and evidence meets its rubric; otherwise use it as secondary.

Simultaneous supported labels not selected as primary are secondary. For example, a patch that misunderstands the task, leaves a hidden test failing, and breaks a regression has primary `REGRESSION` and may have secondary `HIDDEN_TEST_FAILURE` and `MISUNDERSTOOD_REQUIREMENT`. A TRR-resolved patch with a lint finding has no primary label and may record diagnostic secondary `LINT_FAILURE`; the lint metric still fails.

## 4. Annotation quality

- Automated labels may assign only categories with deterministic structured evidence.
- Human-causal labels require a concise rationale and evidence reference.
- A second reviewer checks causal labels without aggregate per-configuration results.
- Disagreement is recorded and adjudicated; the original and final labels remain auditable.
- `UNKNOWN` is not a taxonomy label. If evidence is insufficient for a causal explanation, retain the supported terminal gate label.
- Taxonomy version changes require re-annotation or separately versioned results.
