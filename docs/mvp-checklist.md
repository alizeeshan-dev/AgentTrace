# AgentTrace MVP Checklist

This checklist tracks the minimum system defined by the [project charter](project-charter.md). It records requirements only; Phase 0 does not implement them.

## Required for the research question

- [ ] Pin a small Python repository from a local directory or Git URL to a fixed revision.
- [ ] Admit a bug-fix or small-refactor task only after its baseline, success, and regression expectations are fixed and validated.
- [ ] Build and record one deterministic prepared initial context shared by A, B, and C.
- [ ] Give Configurations B and C identical allowlisted, read-only inspection tools and limits.
- [ ] Generate and validate unified-diff patches.
- [ ] Apply patches only to disposable working copies.
- [ ] Run deterministic verification for trusted, pre-qualified benchmark repositories in disposable Git workspaces through the restricted Windows subprocess runner, with isolated virtual environments where required, hard timeouts, bounded output, and sanitized process environments.
- [ ] Enforce one patch request for A and B; refusals, missing diffs, and invalid responses are terminal unresolved outcomes.
- [ ] Treat invalid, unappliable, policy-rejected, or check-failing initial patches as fixed repair triggers for C; exclude only evidenced infrastructure failures.
- [ ] Enforce one initial patch plus at most one verification-assisted repair for C, with no tools after feedback and a full replacement diff against a clean baseline.
- [ ] Capture prompts, responses, tool calls, patches, verification, status, tokens, cost when available, and latency in structured traces.
- [ ] Run a fixed task set under A, B, and C using the charter's shared controls and frozen denominator, terminal-outcome, and stage-only infrastructure-recovery rules.
- [ ] Export machine-readable results and report resolution, regressions, latency, token cost, and failure categories.

## Required for usability and oversight

- [ ] Configure repositories and tasks locally without changing internal source code.
- [ ] Present the final diff, verification evidence, trace, risks, and usage for human review.
- [ ] Record accept, reject, or needs-changes separately from automated scoring.
- [ ] Document reproducible setup, experiments, architecture, and limitations.
- [ ] Add automated tests for important behavior and security boundaries.
- [ ] Exclude secrets, temporary repositories, generated artifacts, logs, and model responses from Git except for deliberate sanitized fixtures.

## Optional extensions — defer until the MVP experiment runs end to end

- [ ] Additional programming languages or large-repository support.
- [ ] Additional models, providers, verification profiles, or visualization features.
- [ ] Broader benchmarks or repeated-run studies beyond the initial task set.
- [ ] Any separately labelled experimental condition beyond the frozen A/B/C comparison.

The explicit exclusions in the charter are not backlog items and must not be reclassified as MVP work without a documented scope decision.
