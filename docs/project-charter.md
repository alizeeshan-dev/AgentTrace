# AgentTrace Project Charter

## 1. Purpose and research question

AgentTrace is a local research system for evaluating and supervising patches produced by LLM-based software-engineering agents.

> **Research question:** How much do deterministic verification gates and bounded repair loops improve the reliability of LLM-generated software patches?

This charter is the research and engineering contract for the initial version. Later work must preserve the experimental distinctions defined here. Features that do not serve this contract are optional extensions and must not delay the minimum viable research system (MVP).

## 2. Problem

Coding agents can produce patches that look plausible while remaining behaviorally incorrect, failing behavior not visible in the prompt, or introducing regressions in previously working behavior. A convincing explanation or syntactically valid diff is therefore not sufficient evidence of correctness.

The project studies whether deterministic evidence gathered after patch generation, coupled with a tightly bounded opportunity to act on that evidence, makes final patches more reliable.

## 3. Intervention

AgentTrace combines:

- controlled repository inspection through an explicit allowlist of read-only tools;
- generation of proposed changes as unified-diff patches;
- deterministic verification of patches against configured checks;
- structured execution traces covering model actions, tool use, patches, verification, usage, cost, and latency;
- at most one verification-assisted repair attempt; and
- human review of the final patch and its evidence before any change is accepted outside the experiment.

The bounded repair loop consists of an initial patch, deterministic verification, and zero or one repaired patch. A repair is allowed only in Configuration C and only after the initial response fails an eligible deterministic verification gate: patch validation, patch application, patch policy, or a required verification check. Verification of the repaired patch ends the automated loop regardless of its result. Human review is a supervision mechanism, not a second repair loop.

## 4. Supported scope

The initial version supports only:

- small Python repositories whose configured checks can run locally in a practical bounded time;
- bug-fix tasks and small, behavior-preserving refactors;
- repositories supplied as local directories and/or Git URLs;
- proposed changes represented as unified diffs;
- patching and execution in an isolated working copy rather than the source repository; and
- local execution of the system and its experiments.

Each evaluation task must identify a fixed repository revision, a task statement, a prepared initial context, allowed inspection tools for Configurations B and C, deterministic verification commands, and the expected behavioral checks. Before admission to the experiment, the pinned baseline must complete successfully under the verification harness: every designated regression check must pass, and every task-specific check must exhibit its predeclared baseline outcome. The same admitted task definition and repository revision must be used across configurations.

## 5. Experimental contract

### 5.1 Shared controls

For a given task comparison, all configurations must use:

- the same fixed repository revision and task statement;
- the same recorded prepared initial context;
- the same model and model parameters, except where a recorded provider constraint prevents this;
- the same initial-patch response format and generation limits;
- the same patch format and patch-application rules;
- the same verification suite, limits, and isolated execution policy;
- the same definition of task resolution and regression;
- recorded prompts, model outputs, token usage, estimated cost, and wall-clock latency; and
- no human editing of a patch before its automated outcome is measured.

Randomness, retry policy, context construction, and any provider-side differences must be recorded. Apart from Configuration C's one declared repair opportunity, a completed model action is never repeated within or across recovery attempts. Infrastructure failures are reported separately from incorrect patches and must not be silently counted as successful or repaired by adding model attempts.

### 5.2 Configuration A — Direct Patch

1. The model receives the task statement and the shared prepared, non-interactive repository context.
2. The model has no repository tools and cannot request additional context.
3. AgentTrace makes exactly one model request for a patch. That response is terminal even if it is a refusal, malformed, missing, or invalid.
4. The patch is applied and verified only after generation for evaluation.
5. Verification results are never returned to the model, and no repair is permitted.

The prepared context must be deterministic for the same task and must be recorded so runs can be reproduced.

### 5.3 Configuration B — Tool Agent

1. The model receives the same task statement and prepared initial context as Configuration A, then may iteratively inspect the repository using only approved, read-only repository tools.
2. Tool requests and results are recorded and bounded by the experiment's declared tool-step limit.
3. After inspection, AgentTrace makes exactly one model request for a patch. That response is terminal even if it is a refusal, malformed, missing, or invalid.
4. The patch is applied and verified afterward for evaluation.
5. Verification results are never returned to the model, and no repair is permitted.

### 5.4 Configuration C — Verified Agent

1. The model receives the same task statement and prepared initial context as Configurations A and B, and has the same approved repository tools, tool behavior, and pre-patch tool-step limit as Configuration B.
2. AgentTrace makes one initial model request for a patch.
3. The initial patch is applied and deterministically verified.
4. If every required check passes, the initial patch is final and no repair is requested.
5. If the initial response is an invalid diff, cannot be applied, violates patch policy, or fails or times out in a required check for a reason attributable to the proposed patch, AgentTrace returns structured failure feedback and requests one repair response. Patch validation and application are deterministic verification gates for this purpose.
6. The repair opportunity is consumed by the next model response, including a refusal, missing diff, or invalid diff. No additional repository-tool calls are permitted between feedback and that response.
7. A repair diff must be a complete replacement proposal against the clean pinned baseline, not a delta on top of the initial patch. AgentTrace applies it to a fresh clean working copy and verifies it once. Its result is final; no further model repair, tool phase, or hidden retry is allowed.

Structured feedback contains the applicable gate or check identifiers, pass/fail status, exit codes when available, bounded diagnostic summaries, and relevant failure output. It must not include hidden expected patches or human-authored repair instructions. The feedback schema and truncation policy must be fixed before comparative runs and consistent across runs. Failures attributable to the experiment harness or environment are infrastructure failures, not repair triggers.

### 5.5 What the comparisons isolate

- **B versus A** estimates the effect of controlled, iterative repository inspection added to the same initial context.
- **C versus B** estimates the effect of deterministic verification feedback plus one bounded repair opportunity.
- **C final versus C initial** measures within-run repair outcomes, including fixes, unchanged failures, and regressions introduced by repair.

Verification after the final patch is an evaluation measurement in all configurations. Only Configuration C may expose initial verification results to the model.

## 6. Outcomes and hypotheses

### 6.1 Operational outcomes

- **Task resolved:** the final patch applies successfully and produces every predeclared post-patch outcome for the required task-specific and regression checks.
- **Regression-producing patch:** after the verified baseline passed every designated regression check, the patched working copy gives any non-pass result for at least one such check for a reason attributable to the patch. A patch may be both unresolved and regression-producing.
- **Evaluated run:** a planned run on an admitted task once the first model request begins. Model refusal, malformed output, invalid or unsafe patch, patch-application failure, agent/tool-limit exhaustion, and patch-caused verification failure or timeout remain in the denominator and count as unresolved. A missing final patch is not regression-producing because no patched state exists to compare.
- **Infrastructure failure:** a failure outside the model and proposed patch, such as failure to create the working copy or a verification-harness crash that also occurs on the clean baseline. It is excluded from outcome denominators and logged with its evidence. If it occurs before the first model request, the planned run may be restarted once. If it occurs after a model response or tool result has been captured, only the failed non-model stage may be retried once using those captured artifacts; no model action is repeated. If safe stage-only recovery is impossible, or the one recovery also fails, the result remains reported as missing data rather than being repeatedly retried.
- **Final resolution rate:** resolved runs divided by evaluated runs excluding only infrastructure failures, reported by configuration.
- **Regression rate:** evaluated runs with a regression-producing final patch divided by evaluated runs excluding only infrastructure failures, reported by configuration.
- **Conditional regression rate:** regression-producing final patches divided by final patches that applied successfully, reported as a secondary measure so a low patch-production rate cannot be mistaken for safety.
- **Latency:** elapsed wall-clock time from the start of the model run through final verification, reported under one documented timing policy.
- **Token cost:** recorded input and output tokens, plus estimated monetary cost when provider pricing metadata is available. Raw token counts remain the provider-independent measure.

Required checks, their baseline expectations, and their post-patch success criteria must be fixed before comparative runs. Checks used to score final outcomes must be identical across A, B, and C. Results must include counts, denominator exclusions, and uncertainty, not only percentages, and paired task-level comparisons should be used when the same tasks are run in each configuration.

### 6.2 Testable hypotheses

- **H1 — Tool use:** Configuration B has a higher final task-resolution rate than Configuration A.
- **H2 — Verification-assisted repair:** Configuration C has a higher final task-resolution rate than Configuration B; Configuration C's final patches also resolve more tasks than its initial patches.
- **H3 — Regressions:** Configuration C has a lower final regression-producing patch rate than Configuration B.
- **H4 — Resource cost:** Configuration C has higher median end-to-end latency and higher median token usage than Configuration B.

No hypothesis is considered supported merely because one example improves. The evaluation report must disclose the task set, number of evaluated runs, infrastructure-failure exclusions and recovery attempts, observed effect sizes, and uncertainty. Estimated monetary cost is descriptive if pricing data is incomplete or changes over time.

## 7. MVP requirements

### 7.1 Required for the research question

- Register a small Python repository from a local directory or Git URL and pin its revision.
- Admit bug-fix and small-refactor tasks only after validating fixed baseline expectations, success checks, and regression checks.
- Prepare one deterministic, recorded initial context shared by all three configurations.
- Provide Configurations B and C the same allowlisted, read-only repository inspection tools.
- Generate, validate, and apply unified-diff patches to disposable working copies without modifying the source repository.
- Run configured tests, linting, optional type checking, and basic static analysis through a deterministic, isolated verification pipeline with bounded resources and network access disabled during repository-code execution.
- Enforce one patch request in A and B, and one initial plus at most one repair request in C, using the frozen repair trigger and full-replacement diff semantics.
- Capture structured traces for prompts, model responses, tool calls, patch attempts, verification results, token usage, estimated cost when available, latency, and terminal status.
- Run the same controlled task set under A, B, and C, enforce the declared terminal-outcome and infrastructure-failure rules, and export machine-readable results.
- Report task resolution, regressions, cost, latency, failure categories, methods, limitations, and task-level results.

### 7.2 Required for usability and oversight

- Let a local user configure a repository and task without editing internal source code.
- Present the final patch, verification evidence, trace, risks, and usage data for human review.
- Let the reviewer record accept, reject, or needs-changes with notes; this decision does not alter the automated score.
- Provide reproducible local setup and experiment instructions, architecture documentation, and automated tests for important logic.
- Keep generated repositories, logs, model responses, temporary artifacts, and secrets out of version control except for deliberate sanitized fixtures.

The concise, trackable form of these requirements is in [MVP checklist](mvp-checklist.md).

## 8. Optional extensions

Optional extensions are explicitly outside the MVP until the complete A/B/C experiment runs end to end. Examples include support for additional languages, larger repositories, more verification profiles, richer visualizations, more model providers, repeated repairs, broader benchmarks, and remote execution. An optional extension must not change the frozen A/B/C definitions for the primary experiment; if explored, it is a separately labelled experimental condition.

## 9. Explicit exclusions

The initial version makes no commitment to:

- deployment, hosting, or cloud infrastructure;
- autonomous GitHub merging, pushing, or pull-request approval;
- a general shell exposed to the agent;
- multi-agent orchestration;
- model fine-tuning or training;
- full SWE-bench support or claims based on the full benchmark;
- support for languages other than Python;
- automatic execution of untrusted repository code directly on the host;
- more than one verification-assisted repair attempt;
- autonomous acceptance of a patch without human review; or
- production-grade security guarantees or claims.

These exclusions are scope and claim boundaries. They must not be weakened merely to make an experiment pass.

## 10. Definition of project success

The MVP succeeds when another engineer can reproduce a controlled local experiment in which the same declared task set is run under Configurations A, B, and C; every run produces a reviewable patch or a structured terminal failure; verification and repair limits are enforced; source repositories remain unchanged; complete structured traces and resource measurements are retained; and a report compares resolution, regressions, latency, and token cost without overstating the evidence.

Project success means the research question can be answered transparently even if the hypotheses are not supported. A negative or mixed result is a valid outcome. Human acceptance rate, interface polish, or demonstration quality cannot substitute for the defined behavioral measurements.

## 11. Change control

Any later feature must be labelled **required for the research question**, **required for usability**, or **optional extension**. Changes to outcome definitions, task checks, verification feedback, tool access, attempt limits, or configuration-specific context after experiments begin require a documented protocol revision and new, separately identified results; they must not silently replace the contract above.
