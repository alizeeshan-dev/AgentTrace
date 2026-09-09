# AgentTrace

AgentTrace is a local research system for evaluating and supervising software
patches produced by large-language-model agents. It compares controlled agent
configurations, applies generated unified diffs only in disposable Git
workspaces, verifies candidate behavior, and preserves the evidence needed to
analyze correctness, regressions, repair, cost, and repository exploration.

The project is an engineering candidate for an empirical study. Its 15-task
benchmark and core execution paths exist, but the main real-model experiment is
not frozen or complete. Consequently, this repository does not yet claim an
LLM task-resolution result.

## Research problem

Coding agents can generate plausible, syntactically valid patches that still
misunderstand a requirement, fail held-out behavior, or regress code that
previously worked. A patch and its explanation are therefore insufficient
evidence of correctness.

AgentTrace asks:

> **How much do deterministic verification gates and bounded repair loops
> improve the reliability of LLM-generated software patches?**

The broader study also examines whether test-oracle strength, property-based
counterexamples, and spectrum-based fault localization affect repair outcomes
or exploration cost. The complete research contract is in
[`docs/project-charter.md`](docs/project-charter.md), and the focused literature
review is in [`docs/literature/literature-matrix.md`](docs/literature/literature-matrix.md).

## What the system does

For one task, AgentTrace pins a repository revision, creates a clean workspace,
asks a model for a structured action, validates and applies the submitted diff,
runs configured verification gates, and records an ordered trace. In repair
configurations, one verified failure may be converted into a bounded,
sanitized counterexample and returned for one complete replacement patch.

```text
Task + fixed commit
        |
Disposable Git workspace
        |
Structured model actions ──> bounded repository tools
        |
Unified-diff validation and application
        |
Deterministic verification
        |
Optional counterexample ──> one clean-base replacement patch
        |
SQLite records + content-addressed artifacts + JSON/Markdown report
```

Core capabilities include:

- provider-independent model orchestration with a built-in Gemini Developer API
  adapter and a deterministic fake provider for tests;
- structured `list_tree`, `read_file`, `search_code`, and `submit_patch`
  actions, with no unrestricted shell exposed to the model;
- canonical path checks, traversal and symlink-escape prevention, `.git` and
  hidden-test protection, bounded reads/searches, and patch/tool/turn budgets;
- native Windows verification in disposable workspaces with explicit working
  directories, sanitized subprocess environments, bounded output, process-tree
  termination, hard timeouts, and temporary virtual environments where needed;
- required patch, compilation, visible-test, full-test, hidden-test, and
  task-specific Hypothesis gates, plus configurable Ruff, mypy, Bandit, and
  CrossHair/Z3 checks;
- pytest-gremlins mutation qualification to measure benchmark-oracle strength;
- per-test Coverage.py spectra and Ochiai fault-localization rankings;
- a CEGIS-inspired protocol that permanently preserves the initial patch and
  permits at most one replacement patch against the original base commit;
- ordered, redacted run traces; resumable experiment matrices; separate raw and
  derived result namespaces; and deterministic evidence-based run reports;
- a FastAPI service and React interface for benchmark runs, trusted external
  repository tasks, run history, experiment summaries, and report retrieval.

## Experimental configurations

All configurations accept the same task schema and record the same core run
metadata. Model, task statement, base commit, budgets, patch policy, and final
evaluation are intended to remain fixed within a comparison block.

| Configuration | Repository access | Pre-agent evidence | Verification feedback | Repair |
| --- | --- | --- | --- | --- |
| **A — Direct Patch** | Deterministic prepared context only | None | No | None |
| **B — Tool Agent** | Bounded read-only tools | None | No | None |
| **C — Verified CEGIS** | Same bounded tools as B | None | Structured failure evidence | At most one replacement patch |
| **D — Research-Enhanced CEGIS** | Bounded tools | Bounded Ochiai ranking | Tests plus eligible Hypothesis/CrossHair evidence | At most one replacement patch |

The D1, D2, and D3 presets isolate SBFL, Hypothesis, and CrossHair respectively
without duplicating orchestration. Optional techniques are disabled explicitly
when a task has no compatible evaluator-owned profile.

## Architecture and technology stack

The code uses small services around explicit Pydantic contracts rather than an
agent framework.

| Layer | Implementation |
| --- | --- |
| API and configuration | FastAPI, Uvicorn, Pydantic, pydantic-settings |
| Persistence | SQLAlchemy 2, SQLite |
| Model boundary | Provider-neutral `ModelProvider`; Google Gen AI SDK; typed Pydantic actions |
| Repository execution | Git bundles/clones, disposable workspaces, native Windows restricted subprocess runner |
| Verification | pytest, Coverage.py, pytest-cov, Hypothesis, Ruff, mypy, Bandit, optional CrossHair and Z3 |
| Benchmark qualification | YAML manifests and a pytest-gremlins normalization adapter |
| Research evidence | Ochiai SBFL, counterexamples, trace events, content-addressed artifacts, deterministic JSON/Markdown reports |
| Experiment layer | Typed YAML matrices, stable run identifiers, resumable execution, immutable raw exports |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS, React Router, TanStack Table |
| Planned analysis environment | pandas and matplotlib are constrained dependencies; the main analysis has not yet been run |

The persistent research entities are `Repository`, `Task`, `BenchmarkQuality`,
`Run`, `FaultLocalizationResult`, `PatchArtifact`, `VerificationResult`,
`Counterexample`, `TraceEvent`, and `RunReportRecord`.

## Experimental methodology

### Benchmark

The candidate benchmark version is `benchmark-v1.0.0`. It contains 15 small,
deterministic Python repair tasks stored as YAML manifests, Git bundles,
evaluator-owned hidden tests, and known-correct unified diffs. Tasks cover
boundary cases, validation and business rules, parsing, transformations,
algorithms, and API/application logic. Admission requires a fixed base commit,
stable faulty behavior, passing regression checks, inaccessible hidden tests,
and a reference patch that applies and satisfies the required oracle.

Mutation testing is a qualification-time measurement, not a gate run after
each model patch. The pytest-gremlins adapter normalizes detected, surviving,
excluded, skipped, invalid, and unusable mutations into `BenchmarkQuality` and
computes:

```text
mutation score = killed / (killed + survived)
```

Invalid or unusable mutations are recorded separately rather than counted as
survivors. Property and symbolic profiles are evaluator-owned and enabled only
for eligible tasks.

### Verification and outcome

Candidate gates run in fail-fast order:

1. patch application and policy;
2. Python syntax/compilation;
3. targeted visible tests;
4. complete existing/baseline tests;
5. hidden tests;
6. configured Hypothesis properties.

Ruff, mypy, Bandit, and CrossHair are advisory unless a frozen verification
profile declares otherwise. Baseline outcomes are retained so AgentTrace can
distinguish an existing failure from a patch-induced regression. Hidden-test
source and identifiers remain outside model-readable artifacts; any actionable
failure evidence is bounded and sanitized.

The primary metric is:

> **Task Resolution Rate (TRR) = 100 × resolved valid runs / all valid runs**

A valid run is resolved only when its final patch applies, all required
fail-to-pass checks pass, and all pass-to-pass regression checks remain passing.
Provider and local infrastructure failures are excluded with explicit reasons;
refusals, malformed model output, unsafe patches, tool exhaustion, and incorrect
patches remain valid unresolved runs. Secondary measures include first-patch
success, repair success, regression and invalid-patch rates, token use, cost,
latency, tool calls, files/lines exposed, patch size, mutation score, and SBFL
rank. See [`docs/experiment/metrics.md`](docs/experiment/metrics.md) and
[`docs/experiment/experimental-methodology.md`](docs/experiment/experimental-methodology.md).

### Traces and reports

Raw run exports contain redacted task/repository metadata, model and tool
events, P0/P1 artifacts, verification results, counterexamples, SBFL evidence,
usage, cost, latency, and terminal classification. Raw data is immutable;
manual classifications and analyses belong in the separate derived namespace.

For a terminal run, the report service deterministically produces typed JSON
and content-addressed Markdown from stored evidence without another model call.
It is a report of one AgentTrace run, not a repository health audit or a
numerical quality score.

## Current validated evidence

These numbers describe benchmark construction and localization validation, not
LLM repair effectiveness.

| Evidence | Verified value |
| --- | ---: |
| Benchmark tasks | 15 |
| Difficulty distribution | 8 easy, 4 medium, 3 hard |
| Tasks with fixed commit, reproduced hidden failure, applicable reference patch, and passing corrected visible/hidden suites | 15/15 |
| Tasks with a Hypothesis profile | 2/15 |
| Tasks with a symbolic profile | 1/15 |
| Ochiai known-fault Top-1 containment | 9/15 (60%) |
| Ochiai known-fault Top-5 containment | 15/15 (100%) |
| Ochiai known-fault Top-10 containment | 15/15 (100%) |

The 15-task checks are parameterized in
[`backend/tests/test_benchmark_tasks.py`](backend/tests/test_benchmark_tasks.py),
and the localization evidence is summarized in
[`docs/phase-10-readiness.md`](docs/phase-10-readiness.md).

No completed pytest-gremlins scores are currently available, so no mutation
score is reported. The intended main matrix is 15 tasks × A/B/C/D = 60 runs,
but [`experiments/main.example.yaml`](experiments/main.example.yaml) remains an
unfrozen template and `experiments/main.yaml` does not exist. There are no
frozen real-model resolution, regression, repair, token, cost, or latency
results to report. Earlier deterministic-provider runs were integration
fixtures and must not be interpreted as model-performance evidence.

## Setup

The current constrained host environment was prepared with Python 3.13.5;
the package metadata requires Python 3.12 or newer. Git is required. Node.js
and npm are needed only for the frontend.

From the repository root in PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c constraints/main-experiment.txt -e ".[dev,verification,qualification,analysis]"
```

The constraints file fixes the versions used by the current Windows research
environment. Generated databases, workspaces, artifacts, and model responses
are written under `.agenttrace/`, which is ignored by Git.

### Gemini credential

The backend uses the Gemini Developer API for real-model runs. Create the local
environment file and replace its placeholder; do not add quotes:

```powershell
Copy-Item -LiteralPath .env.example -Destination .env
```

```dotenv
GEMINI_API_KEY=replace_with_your_real_key
```

`.env` is ignored by Git. The key is loaded as a Pydantic `SecretStr` and is
excluded from traces, artifacts, logs, experiment YAML, and verification
subprocess environments. Model selection and request parameters are under
`model` in an experiment YAML file; the one-task example is
[`experiments/gemini-smoke.yaml`](experiments/gemini-smoke.yaml). Confirm the
model identifier and pricing snapshot before collecting research data.

## Usage

### Start the backend

```powershell
.\.venv\Scripts\uvicorn.exe app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

In another PowerShell window, check the service and open the interactive API
documentation at `http://127.0.0.1:8000/docs`:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

### Register a benchmark task

Qualification validates the baseline and reference patch, runs
pytest-gremlins, persists `BenchmarkQuality`, and makes the task available to
the API:

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m app.benchmark.cli benchmark/tasks/boundary-empty-input.yaml --benchmark-root benchmark --state-dir .agenttrace
```

### Start the frontend

```powershell
Set-Location frontend
npm ci
npm run dev
```

Vite serves the interface at `http://127.0.0.1:5173` by default. The frontend
uses `http://127.0.0.1:8000` unless `VITE_API_BASE_URL` is set.

### Run one configured real-model task

The selected task must already be qualified in the same state directory. This
command makes a real provider request and may consume quota:

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m app.experiments.cli --config experiments/gemini-smoke.yaml --benchmark-root benchmark --state-dir .agenttrace
```

For external repositories, use the metadata-first trust workflow documented in
[`docs/external-repositories.md`](docs/external-repositories.md). AgentTrace
does not automatically install an external repository's dependencies.

### Reports

For a completed run with ID `<run_id>`:

```http
POST /runs/<run_id>/report
GET  /runs/<run_id>/report
GET  /runs/<run_id>/report/markdown
```

The first request creates an idempotent report artifact; the other requests
return its structured and Markdown representations.

### Development checks

The backend test suite is organized in 26 modules covering schemas,
repositories/workspaces, mutation parsing, SBFL, agent configurations,
verification, CEGIS invariants, tracing, experiments, external repositories,
API behavior, and reports.

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe

Set-Location frontend
npm run lint
npm run build
```

## Repository structure

```text
AgentTrace/
├── backend/
│   ├── app/
│   │   ├── agent/                 # providers, typed actions, tools, patch policy
│   │   ├── benchmark/             # task loading and qualification
│   │   ├── cegis/                 # counterexample extraction and one-repair loop
│   │   ├── configurations/        # shared A/B/C/D contracts and D ablations
│   │   ├── experiments/           # matrices, stable IDs, resumability, raw storage
│   │   ├── fault_localization/    # per-test spectra and Ochiai ranking
│   │   ├── mutation/              # pytest-gremlins adapter and normalization
│   │   ├── reports/               # deterministic JSON/Markdown run reports
│   │   ├── repositories/          # Git, path policy, external registration
│   │   ├── traces/                # ordered trace assembly and export
│   │   ├── verification/          # Windows runner and verification gates
│   │   ├── api.py                 # HTTP routes
│   │   ├── config.py              # typed environment settings
│   │   └── main.py                # FastAPI application
│   └── tests/                     # targeted and integration-level backend tests
├── benchmark/
│   ├── tasks/                     # 15 YAML task manifests
│   ├── repositories/              # immutable Git bundles
│   ├── hidden_tests/              # evaluator-only test suites
│   ├── patches/                   # known-correct reference patches
│   ├── property_profiles/         # Hypothesis configuration
│   └── symbolic_profiles/         # eligible CrossHair contracts
├── constraints/                   # constrained Python environment
├── docs/                          # charter, methodology, phase records, operations
├── experiments/                   # pilot and unfrozen experiment YAML definitions
├── frontend/                      # React/TypeScript research interface
├── .env.example                   # credential template without secrets
├── agentrace.md                   # complete project specification
└── pyproject.toml                 # Python package and tool configuration
```

## Limitations and future work

- Native Windows subprocess controls are weaker than VM or container isolation.
  AgentTrace is restricted to curated benchmark code or external repositories
  the user explicitly trusts; it is not a sandbox for arbitrary untrusted code.
- The benchmark contains small Python repair tasks and is not SWE-bench. Its
  results will not establish performance on large, multi-language repositories.
- External tasks lack benchmark hidden tests, known-correct patches, mutation
  qualification, difficulty labels, and evaluator-owned property/symbolic
  profiles unless separately curated. Passing their available checks is weaker
  evidence than benchmark resolution.
- Mutation qualification with pytest-gremlins still requires completion and
  persistence for all 15 tasks before the benchmark can be frozen.
- The Windows environment manifest/fingerprint, exact real model snapshot,
  pricing, and `experiments/main.yaml` remain unfrozen. The 60-run main study
  and its pandas/matplotlib analysis have not begun.
- Hypothesis profiles cover two tasks and the symbolic profile covers one;
  absence of a discovered property or CrossHair counterexample is not proof of
  correctness.
- The one-repair ceiling is intentional experimental control, not an attempt to
  maximize autonomous solve rate. Final patches still require human review.

The next research step is to complete Windows migration validation and
pytest-gremlins qualification, freeze the environment/model/configuration, and
then execute the predeclared matrix without changing prompts, tasks, budgets,
or verification behavior in response to observed performance.
