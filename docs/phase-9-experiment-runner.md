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

Keep provider credentials in the process environment, outside the YAML and artifacts. Implement a
provider-neutral factory `create_provider(slot: ExperimentSlot) -> ModelProvider`, then use:

```powershell
$env:PYTHONPATH = "backend"
python -m app.experiments.cli --config experiments/pilot-real.example.yaml --benchmark-root benchmark --state-dir .agenttrace-real --provider-factory your_adapter:create_provider
```

First copy the example to a deliberately frozen file and replace its model placeholders. Use a
separate experiment ID and state directory for real results. The provider adapter is responsible
for controlled credentials and cost limits; AgentTrace does not hard-code a provider SDK or secret
names.
