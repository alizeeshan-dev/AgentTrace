# Benchmark Task Format

## Normative artifact

[task.schema.json](task.schema.json) is the normative machine-readable format for benchmark-task version 1. JSON documents validate directly; YAML documents are acceptable only if parsing them produces the same JSON data model and that data validates against the schema.

This static JSON Schema fixes a research data contract without choosing Pydantic models, database tables, API types, or application architecture. Those are later-phase implementation decisions.

## Field semantics

| Field | Meaning and invariant |
| --- | --- |
| `task_id` | Globally unique, stable identifier. It must not encode a model or configuration. |
| `repository` | Canonical HTTPS Git URL or portable relative local path. Absolute host paths, credentials, embedded tokens, query strings, fragments, and mutable branch names are forbidden. |
| `base_commit` | Full lowercase 40-hex commit object ID. The evaluator checks out this exact commit; a tag or branch is insufficient. |
| `task_type` | Exactly `bug_fix` or `refactor`. |
| `title` | Short user-facing task name with no newline. |
| `description` | Complete task statement given identically to A, B, and C. It must describe observable intent without leaking a solution or hidden assertion. |
| `visible_test_command` | Curator-authored command for checks whose source and bounded failure output may be available to the agent. |
| `hidden_test_command` | Curator-authored evaluator-only command used for final FAIL_TO_PASS and PASS_TO_PASS scoring. Its text, tests, identifiers, and output are withheld from the model. |
| `allowed_paths` | Non-empty set of repository-relative files or directory prefixes to which a patch may write. |
| `forbidden_paths` | Non-empty set of repository-relative files or directory prefixes that a patch may not change. Hidden-test locations must be covered when they live in the checkout and separately excluded from inspection tools. |
| `timeout_seconds` | Per-command wall-clock limit from 1 through 900 seconds, identical across configurations for the task. |
| `difficulty` | Curator estimate: `easy`, `medium`, or `hard`, assigned before agent runs under the rubric in [task selection](task-selection.md). |
| `tags` | Non-empty, lowercase kebab-case descriptors for behavior and task family; never model or outcome labels. |

## Repository and path rules

- Local repository locators must be portable relative paths resolved from the frozen experiment workspace. A task manifest never contains a machine-specific absolute path.
- HTTPS locators must contain a host and repository path and must not contain URI user information, query parameters, or fragments. Schema validation is supplemented by semantic validation before a manifest is frozen so encoded or otherwise disguised credentials are rejected.
- Paths use `/` separators and are interpreted lexically relative to the clean repository root after symlink-safe resolution.
- A path ending in `/` denotes that directory and every descendant; a path without a trailing `/` denotes exactly one file. Matching is segment-aware, so `src/api/` does not match `src/api_old/`.
- `..`, absolute paths, drive prefixes, backslashes, repeated separators, and glob metacharacters are invalid.
- Every proposed write must match at least one allowed entry and no forbidden entry. Forbidden takes precedence over allowed.
- Path validation must occur before patch application and again after canonical/symlink-safe resolution. Schema validation alone is not a filesystem security boundary.

## Command and visibility rules

The manifest is evaluator-owned. The agent receives the title, description, common prepared context, and the applicable write policy, but never the hidden command or hidden test artifacts. `forbidden_paths` governs patch writes; hidden artifacts must additionally live outside approved inspection roots or on a read denylist enforced by repository tools.

Command fields are trusted curator data, not model-generated shell. Their presence in the schema does not authorize arbitrary execution: the verification layer must limit execution to trusted, pre-qualified benchmark repositories; use a disposable workspace, explicit working directory, sanitized environment, bounded output, and hard timeout; and never interpolate model output into a command.

At admission, the hidden command is run repeatedly on the clean baseline. Stable baseline-failing task checks become FAIL_TO_PASS; stable baseline-passing checks become PASS_TO_PASS. The same inventory is used for every configuration. A final exit code alone is not enough—the evaluator must preserve per-test identities and statuses in a structured or machine-readable test report.

## Illustrative shape

This is a format example, not an admitted task and not a real repository citation:

```yaml
task_id: parser-empty-input-001
repository: examples/parser-repository
base_commit: 0000000000000000000000000000000000000000
task_type: bug_fix
title: Handle empty input in the record parser
description: Return an empty record collection when the parser receives an empty string, while preserving non-empty parsing behavior.
visible_test_command: pytest -q tests/visible
hidden_test_command: pytest -q evaluator_tests/parser_empty --junitxml=.agenttrace-results/hidden.xml
allowed_paths:
  - src/parser.py
forbidden_paths:
  - tests/
  - evaluator_tests/
  - .agenttrace-results/
timeout_seconds: 120
difficulty: easy
tags:
  - edge-case
  - parsing
```

The all-zero commit deliberately makes this documentation example non-admissible; a real task must reference an existing commit and pass every criterion in [task selection](task-selection.md).

## Immutability

Once the main corpus is frozen, changes to any manifest field create a new task revision and require baseline requalification. The experiment records content hashes for the manifest, prepared context, test inventory, and evaluator artifact so a mutable file cannot silently alter an outcome.

## Binding experimental artifacts

Prepared context, evaluator artifacts, environment definitions, and tool/verification policies are experimental controls rather than intrinsic task fields. They therefore do not expand the closed 13-field task schema. Instead, [corpus-lock.schema.json](corpus-lock.schema.json) defines a `tasks` object keyed by `task_id`, making duplicate bindings structurally impossible, with each value containing:

- a portable task-manifest artifact identifier and SHA-256;
- a prepared-context artifact identifier and SHA-256;
- a hidden-evaluator artifact identifier and SHA-256;
- a baseline-inventory artifact identifier and SHA-256;
- a dependency/environment artifact identifier and SHA-256; and
- stable tool-policy and verification-profile identifiers.

The corpus-lock loader must reject duplicate JSON object keys before schema validation. It then resolves every artifact identifier literally under the designated corpus root, rejects symlinks or canonical paths that escape that root, verifies that the bytes match the recorded SHA-256, validates each task manifest against task schema v1, and confirms the manifest's `task_id` equals its key in the lock. These cross-file checks are mandatory semantic validation because JSON Schema cannot establish them alone.

The corpus lock is frozen with the task list. Any binding or hash change creates a new protocol/corpus version rather than silently changing a valid task manifest.
