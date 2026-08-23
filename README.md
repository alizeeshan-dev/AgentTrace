# AgentTrace

AgentTrace is a local research system for evaluating whether deterministic
verification and one bounded repair opportunity improve LLM-generated software
patches. Phases 0 and 1 freeze the research contract and experimental design.
Phase 2 provides the safe repository and persistence foundation. Phase 3 adds
the benchmark-qualification pipeline, Phase 4 adds pre-agent Ochiai evidence,
Phase 5 adds the direct and constrained-tool patch-generation baselines, and
Phase 6 adds the deterministic native-Windows verification oracle. Phase 7 adds
Configuration C's single counterexample-guided replacement opportunity. Phase 8 adds the
research-enhanced Configuration D and ablations. Phase 9 adds canonical traces, immutable raw
exports, and the resumable experiment runner. Phase 10 expands the candidate benchmark to 15
tasks and adds the integration, reproducibility, and security checks summarized in
`docs/phase-10-readiness.md`.

## Implemented foundation

- FastAPI application factory with a health endpoint and structured JSON logs;
- strict Pydantic research schemas and SQLAlchemy/SQLite persistence;
- immutable local Git snapshot registration and metadata-first public HTTPS Git registration;
- independent disposable clones at recorded commits;
- canonical, bounded repository reads with traversal, link, hidden-evaluator,
  and `.git` protections;
- per-run content-addressed artifact storage for logs, patches, coverage, and
  future verification/model outputs;
- versioned YAML benchmark manifests with evaluator-owned hidden tests;
- fifteen deterministic repair tasks and known-correct patches;
- qualification gates for baseline reproduction and corrected behavior;
- a `pytest-gremlins` adapter that records reproducible, explicitly
  classified mutation evidence in `BenchmarkQuality`;
- per-test Coverage.py contexts, test-by-line spectra, and auditable Ochiai
  rankings persisted through `FaultLocalizationResult`;
- provider-neutral structured model actions with an offline fake provider;
- a built-in Gemini Developer API adapter using required structured function calls;
- deterministic direct-patch context and a bounded, shell-free tool agent;
- unified-diff policy validation and orchestrator-only disposable application.
- baseline-aware, fail-fast verification for curated benchmarks and explicitly
  trusted external repositories in disposable Git workspaces through a restricted Windows
  subprocess runner, with isolated virtual environments where required,
  sanitized environments, bounded output, and hard timeouts;
- evaluator-private deterministic Hypothesis properties with shrunk evidence;
- advisory Ruff, mypy, Bandit, and explicitly configured CrossHair/Z3 search.
- typed, sanitized counterexamples and a cumulative-budget CEGIS-style repair;
- separate P0/P1 artifacts, clean-base P1 application, and complete re-verification.
- a common A/B/C/D configuration contract with task-aware SBFL, Hypothesis, and CrossHair flags;
- canonical redacted trace assembly and deterministic database-independent JSON export;
- deterministic evidence-based JSON and Markdown reports for completed runs;
- stable experiment run IDs, post-hoc A/B verification, resumability, and failure classification.

The portable Phase 1 benchmark manifest remains defined by
`docs/experiment/task.schema.json`. Runtime database entities are deliberately
separate because they include local operational data such as canonical source
paths and experiment relationships.

## Development

Python 3.12 or newer and Git are required.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c constraints/main-experiment.txt -e ".[dev,verification,qualification,analysis]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe
```

Runtime state defaults to `.agenttrace/`, which is ignored by Git. Copy
`.env.example` to `.env` only for local overrides; never place credentials or
provider secrets in project files.

The service can be started locally with:

```powershell
.\.venv\Scripts\uvicorn.exe app.main:app --app-dir backend
```

Pilot qualification is curator-only and runs fixed manifest commands in a
disposable clone. Install the qualification extra and run mutation testing
natively on Windows:

```powershell
python -m pip install -e ".[dev,qualification]"
python -m app.benchmark.cli benchmark/tasks/boundary-empty-input.yaml `
  --benchmark-root benchmark --state-dir .agenttrace
```

The qualification pipeline uses pytest-gremlins only during benchmark
qualification; it does not mutation-test every LLM patch. Candidate
verification is a separate Phase 6 service using the restricted native Windows
runner. External public repositories can be registered metadata-first and remain
non-executable until explicit user trust is recorded. AgentTrace does not claim
that native subprocess restrictions safely sandbox arbitrary untrusted code.
See `docs/verification.md` for the execution boundary,
`docs/external-repositories.md` for the external workflow, and the frozen Windows
environment-fingerprint contract.

Qualified tasks can be localized before any model call:

```powershell
.\.venv\Scripts\python.exe -m app.fault_localization.cli `
  benchmark/tasks/boundary-empty-input.yaml `
  --benchmark-root benchmark --state-dir .agenttrace --top-k 10
```

The coverage artifact contains production line observations and pass/fail
outcomes. Hidden tests use opaque identifiers; their source, paths, node IDs,
assertions, and output are not persisted. Phase 5 does not expose either raw or
ranked SBFL evidence; that evidence is reserved for a later configuration.

Phase 5's library entry point is `app.agent.AgentRunService`. It implements
Configurations A and B only. A candidate marked `patch_submitted` has passed
patch-policy and clean-application checks, but that service has not tested it;
the Phase 6 verifier and Phase 7 CEGIS service remain separate boundaries. See
`docs/phase-5-agent.md` for the protocol boundary.

Configuration C is exposed as `app.cegis.ConfigurationCService`. It returns at
most one sanitized counterexample and requests at most one complete replacement
patch. See `docs/phase-7-cegis.md` for the state and evidence boundaries.

The Phase 9 offline integration matrix is defined in `experiments/pilot.yaml` and runs with:

```powershell
$env:PYTHONPATH = "backend"
python -m app.experiments.cli --config experiments/pilot.yaml --benchmark-root benchmark --state-dir .agenttrace --fake-known-correct
```

This fixture uses evaluator-provided known-correct patches and is not a model-quality result. See
`docs/phase-9-experiment-runner.md` for raw export, resume, and real-provider adapter instructions.

Experiment YAML records `model.provider` as a frozen input. The configured
identifier must equal the active adapter's `provider_name`; changing providers
therefore changes stable run IDs and cannot silently reuse earlier results.
The proposed 60-cell main matrix is `experiments/main.example.yaml`, but it is
not a final configuration until the selected model, pricing snapshot, Windows
environment fingerprint, and remaining Phase 10 inputs are frozen.

## Real Gemini provider

Create the ignored local credential file and replace its placeholder without
quotes:

```powershell
Copy-Item .env.example .env
```

```dotenv
GEMINI_API_KEY=replace_with_your_real_key
```

The model is selected under `model.model` in `experiments/gemini-smoke.yaml`.
After the task is qualified in `.agenttrace`, run one Configuration A task with:

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m app.experiments.cli --config experiments/gemini-smoke.yaml --benchmark-root benchmark --state-dir .agenttrace
```

See `docs/gemini-provider.md` for structured-action behavior, supported request
parameters, controlled errors, and the separate pricing configuration.
