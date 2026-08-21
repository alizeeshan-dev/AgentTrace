# Phase 2 foundation design

## Boundaries

Phase 2 records research data and prepares disposable repositories. It does not
generate patches, invoke models, run tests, localize faults, repair candidates,
or implement a frontend or deployment target.

The foundation has four explicit layers:

1. `schemas` validates research records at process boundaries.
2. `db` persists the nine research entities in SQLite through SQLAlchemy.
3. `repositories` and `artifacts` enforce filesystem and Git safety without
   depending on the web layer.
4. `services` joins registration and task loading to persistence.

Application construction is side-effect free. Database initialization and
runtime-directory creation remain explicit operations so imports and schema
tests do not unexpectedly write local state.

## Repository and workspace contract

A local registration resolves the exact Git root and records the canonical
runtime path, full 40-character commit SHA, declarative Python version when
available, and curator-provided test command. Its identifier represents the
canonical source plus pinned commit, so registrations are immutable snapshots.

Runs use independent `git clone --no-hardlinks` checkouts with their own `.git`
directory. This intentionally avoids Git worktrees, which would add metadata to
the original repository. Every workspace is detached and reset to the recorded
commit; cleanup is restricted to a validated direct child of the configured
workspace root. Source repositories may not overlap workspace or artifact
roots.

Repository paths are literal normalized POSIX-relative paths. Canonical
resolution is checked both before and after following filesystem links. `.git`
is always protected. Phase 1 defines `forbidden_paths` as a patch-write policy,
so hidden evaluator locations are passed separately as a read denylist from the
frozen evaluator/tool-policy artifacts. Tree listing does not expose denied
locations and never descends through symlinks or junctions.

## Reconciliation with earlier research documents

The Phase 1 13-field task manifest remains frozen and unchanged. The persisted
`Task` entity is a separate runtime/research record matching the current core
data model; no local absolute source path is written into portable manifests.
`Run.failure_category` remains nullable text because Phase 1 and the current
roadmap contain different taxonomy versions that must be versioned explicitly
before experiments.

The current roadmap expands the earlier A/B/C study to eventual A-D conditions,
and differs on post-feedback inspection and hidden-test-derived feedback. Those
protocol questions are deliberately not encoded in Phase 2 and must be resolved
through an explicit methodology revision before the corresponding later phase.
