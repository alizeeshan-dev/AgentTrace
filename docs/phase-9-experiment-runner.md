# Phase 9 experiment runner

The experiment runner takes a strict YAML matrix and derives each run ID from the complete
frozen task, provider, model, budget, verification, and condition inputs. The configured
`model.provider` must match the active adapter's `provider_name`. Running the same file again skips
terminal rows and executes only missing rows. An incomplete database row is reported as blocked;
it is never overwritten. Infrastructure exclusions remain distinct from agent failures.

Configurations A and B are verified post-hoc after patch submission. The verifier result is not
returned to the provider and cannot cause another model turn. C and D retain their bounded CEGIS
protocol.

Complete redacted run exports are created once beneath
`.agenttrace/experiments/<experiment_id>/raw`. Each file contains the runner outcome plus the
database-independent trace export, patches, gates, counterexamples, SBFL evidence, metrics, and
artifact hashes. Human failure annotations and later analyses belong under the sibling `derived`
directory. Raw records reject overwrite.

## Offline pilot

The tasks must already be qualified and Configuration D requires its persisted Phase 4 SBFL
result. Then run the exact 12-cell offline integration pilot with:

```powershell
$env:PYTHONPATH = "backend"
python -m app.experiments.cli --config experiments/pilot.yaml --benchmark-root benchmark --state-dir .agenttrace --fake-known-correct
```

`--fake-known-correct` deliberately supplies the evaluator's correct patch. It validates plumbing,
not model quality, and must never be reported as a real-model experiment. For B/C/D it performs
one bounded `read_file` action first so the constrained-tool trace path is exercised.

To inspect the stable plan without executing it:

```powershell
$env:PYTHONPATH = "backend"
python -m app.experiments.cli --config experiments/pilot.yaml --benchmark-root benchmark --state-dir .agenttrace --fake-known-correct --dry-run
```

## Real-provider pilot

The built-in provider is selected with `model.provider: gemini` and reads
`GEMINI_API_KEY` from the ignored local `.env` file. Run a configured real
matrix without a provider-factory argument:

```powershell
$env:PYTHONPATH = "backend"
python -m app.experiments.cli --config experiments/pilot-real.example.yaml --benchmark-root benchmark --state-dir .agenttrace-real
```

Use a separate experiment ID and state directory for real results. A custom
provider can still be supplied through `--provider-factory MODULE:FUNCTION`,
but Configurations A--D remain coupled only to the provider-neutral interface.
