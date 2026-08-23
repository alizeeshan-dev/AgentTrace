# Deterministic Windows verification boundary

AgentTrace executes repository code natively on Windows. Benchmark repositories
are trusted, controlled, and pre-qualified. Public HTTPS Git repositories may be
registered as immutable snapshots, but their code remains blocked until the
user explicitly acknowledges local-execution trust. Native subprocess isolation
is weaker than VM/container isolation; the verifier is not a sandbox for
arbitrary untrusted third-party code.

For each baseline or candidate verification, AgentTrace creates a disposable
Git workspace at the task's recorded base commit. The original benchmark
repository is never executed or modified. Verification uses a dedicated
temporary Python virtual environment where required and invokes only
evaluator-configured commands through the restricted subprocess runner.

The runner supplies explicit argument arrays and an explicit workspace working
directory, enforces hard timeouts, terminates timed-out process trees, bounds
captured output, and passes a sanitized allowlisted environment. Provider API
keys, authorization values, `.env` contents, unrelated host secrets, and the
parent process environment are not forwarded to repository processes. Hidden
tests remain evaluator-owned and outside agent-readable repository paths.
External tasks do not acquire hidden tests merely by registration; only their
explicitly configured pytest command is used. If no command is configured, the
result is recorded as `verification_not_configured`, never as a pass.

The required candidate gates run in fail-fast order:

1. patch application;
2. Python syntax/compilation;
3. targeted visible tests;
4. complete repository/baseline tests;
5. hidden tests;
6. an explicitly configured Hypothesis property profile.

Ruff, mypy, Bandit, and explicitly configured CrossHair/Z3 profiles are
advisory unless the frozen verification profile says otherwise. Baseline gate
results are retained separately so a previously passing check that fails after
the patch is recorded as a regression. Hidden and property source remains
evaluator-only; normalized property artifacts contain only bounded, sanitized,
shrunk counterexample evidence.

The frozen main experiment records a Windows environment manifest and stable
environment fingerprint as its immutable verification-environment identity.
The manifest captures the Windows and Python versions, verification-tool versions,
dependency lock hash, AgentTrace source commit, benchmark version, and
verification profile.

After the migration has been validated and committed, materialize it once with:

```powershell
$env:PYTHONPATH = "backend"
python -m app.experiments.environment_cli `
  --benchmark-version benchmark-v1.0.0 `
  --verification-profile deterministic-v1 `
  --output experiments/freeze/windows-environment.json
```

The command refuses a dirty source tree or an existing destination. The
generated fingerprint is then copied into the frozen experiment configuration.
