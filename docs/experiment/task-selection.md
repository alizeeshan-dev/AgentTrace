# Initial Benchmark Task Criteria

## 1. Intended population

The initial benchmark represents small, locally reproducible Python maintenance tasks: focused bug fixes and behavior-preserving refactors with deterministic tests, bounded patches, and no required network or specialized hardware. It is a deliberately narrow sample, not a miniature claim of full SWE-bench coverage.

Candidate families include boundary conditions, null/empty input, data transformations, validation rules, small API behavior defects, simple algorithmic errors, and tightly scoped refactors.

## 2. Required admission criteria

A task may enter only when all of the following are true before agent runs:

### Repository and reproducibility

- The source is a small Python repository available through a stable HTTPS Git URL or portable relative local source.
- `base_commit` resolves to the exact full commit recorded in the manifest.
- The repository license and benchmark use permit local study and redistribution of task metadata or sanitized evaluator fixtures as applicable.
- Dependencies can be installed in the planned isolated environment without credentials, paid services, privileged host changes, or network access during test execution.
- Setup, visible checks, and hidden checks fit the declared per-command timeout and planned resource limits.
- No secret, personal data, generated model response, machine-specific path, or mutable credential is present in the manifest or fixture.

### Task statement and scope

- The title and description identify an observable defect or refactor goal without prescribing the implementation.
- Two curators agree the statement is sufficiently specified for an experienced Python engineer and that reasonable solutions are not rejected by implementation-specific assertions.
- The reference solution changes at most five files and at most 200 added-plus-removed lines, excluding curator-only evaluator artifacts. Independently, every generated candidate is subject to the same global five-file/200-line deterministic patch policy. Tasks for which a reasonable alternative solution may need more are excluded before agent runs.
- Necessary writes can be expressed by finite `allowed_paths`; tests, evaluator artifacts, repository metadata, dependency locks not required by the task, and generated result directories are forbidden as appropriate.
- The task does not require deployment, remote accounts, GUI interaction, large data, specialized hardware, multi-repository coordination, or changes outside the checked-out working copy.

### Behavioral oracle

- A curator-held reference patch or equivalent reference outcome exists and is never exposed to the agent.
- On three fresh baseline runs, the hidden evaluator reports the identical test inventory and stable outcomes.
- At least one task-specific FAIL_TO_PASS check is non-passing at baseline. For a refactor, this may be a predeclared structural/acceptance check while behavioral regression checks remain passing.
- At least one meaningful PASS_TO_PASS regression check passes at baseline.
- The reference patch applies cleanly, makes every required FAIL_TO_PASS check pass, preserves every PASS_TO_PASS check, and passes all required visible/lint/type/static gates.
- Hidden checks exercise behavior not fully duplicated by visible checks and are inaccessible through approved repository tools.
- Tests do not depend on wall-clock time, uncontrolled randomness, run order, external network, user-specific state, or stale caches. Randomized tests use a fixed recorded seed.
- A clean-baseline control distinguishes patch-caused timeout/failure from a broken harness.

### Metadata

- The manifest validates against [task schema version 1](task.schema.json).
- Tags describe the behavior family and domain before runs.
- Difficulty is assigned by expected experienced-engineer effort: `easy` up to 30 minutes, `medium` over 30 minutes through 2 hours, and `hard` over 2 through 4 hours. Tasks estimated above 4 hours are excluded.
- Manifest, prepared context, hidden evaluator, baseline inventory, dependency/environment definition, and reference outcome are content-hashed before the corpus freeze; the non-secret bindings required for execution are recorded in a valid [corpus lock](corpus-lock.schema.json).

## 3. Exclusion criteria

A task may not enter if any of these apply:

- the issue statement is ambiguous, contradictory, solution-leaking, or only understandable from inaccessible discussion;
- the evaluator accepts only the original maintainer's implementation when other reasonable solutions should pass;
- the baseline or reference outcome is flaky across three fresh runs;
- the task has no stable failing acceptance check or no meaningful passing regression check;
- success requires internet access during tests, a paid/external service, credentials, a large dataset, GUI interaction, specialized hardware, or privileged host access;
- dependency resolution is mutable or cannot be reproduced under the frozen environment definition;
- the expected patch exceeds five production files or 200 changed production lines;
- the change is primarily a new feature, broad redesign, dependency migration, generated-code update, documentation-only edit, deployment task, or security claim requiring production assurance;
- hidden tests or their outputs are inspectable through allowed tools, or the agent can satisfy the task by editing tests/evaluator artifacts;
- the task depends on another repository, service state, current date, nondeterministic ordering, or an unavailable upstream artifact;
- the repository cannot be legally or practically used for the intended local experiment; or
- any A/B/C configuration would receive materially different task content or final scoring.

## 4. Admission procedure

1. Draft the manifest and curator-only reference outcome.
2. Review the statement, path policy, and tests for leakage and implementation overfitting.
3. Validate the manifest against the JSON Schema.
4. Build a fresh isolated baseline and run setup plus all checks three times.
5. Freeze the FAIL_TO_PASS/PASS_TO_PASS identities from the stable baseline inventory.
6. Apply the reference patch in a new clean copy and confirm every required check passes.
7. Confirm hidden artifacts cannot be read or changed under the approved tool/path policy.
8. Assign difficulty and tags without viewing any AgentTrace outcome.
9. Record hashes and obtain two-curator approval.
10. Add the task to the corpus freeze list. After freeze, apply the removal rules in the methodology rather than editing a task in place.

Passing admission shows that a task is reproducible and scoreable; it does not prove that its tests fully specify correct behavior.
