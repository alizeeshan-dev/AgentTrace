# Deterministic verification boundary

Phase 6 runs benchmark repository code only inside a disposable Docker
container. The verifier pins the inspected image ID, mounts a fresh checkout
read-only, mounts only the current task's evaluator material, disables the
network, runs as a numeric non-root user, drops capabilities, enables
`no-new-privileges`, uses a read-only root filesystem, and bounds CPU, memory,
processes, output, and wall time. The Docker socket and host environment are
never mounted.

The required candidate gates are applied patch, Python compilation, visible
tests, complete repository tests, hidden tests, and an explicitly configured
Hypothesis property profile. They run fail-fast. Ruff, mypy, Bandit, and an
explicit CrossHair/Z3 profile are advisory. Baseline gates are retained
separately so a previously passing test that fails after the patch is recorded
as a regression. Hidden and property source remains evaluator-only; normalized
property artifacts contain only bounded, shrunk counterexample evidence.

These restrictions materially reduce risk, but container isolation is not a
formal security guarantee. The Docker daemon, kernel, verifier image, evaluator
files, and AgentTrace host process remain trusted computing-base components.

Build the Linux-compatible image with:

```text
docker build -f docker/verification/Dockerfile -t agentrace-verifier:phase6 docker/verification
```

The service refuses to verify when Docker or the configured image is missing;
it records an infrastructure failure instead of running repository code on the
host.
