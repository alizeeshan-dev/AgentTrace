# Configuration C bounded repair protocol

Configuration C uses the same constrained tool-agent protocol as Configuration
B for its initial candidate. It then follows this fixed state flow:

```text
P0_GENERATION
  -> P0_VERIFICATION
     -> PASS: FINAL
     -> INFRASTRUCTURE/PROVIDER FAILURE: FINAL (no repair)
     -> CANDIDATE FAILURE
        -> COUNTEREXAMPLE_CREATED
        -> REPAIR_STARTED
        -> optional bounded repository tools
        -> P1_GENERATED
        -> RESET_TO_BASE
        -> P1_FULL_VERIFICATION
        -> FINAL
```

P0 and P1 are immutable `PatchArtifact` rows with attempt numbers 1 and 2.
The repair response must be a complete replacement unified diff against the
recorded base commit. P1 is policy-checked after resetting a disposable
workspace, and the verification service independently applies it to another
fresh base checkout. A failed P1 is terminal; no third model patch is possible.

Only genuine candidate failures produce a `Counterexample`. Hidden failures
contain counts and generic behavioral symptoms, never hidden identifiers,
paths, assertions, logs, or source. Hypothesis evidence retains its final
shrunk input. Counterexample feedback is bounded, valid JSON and explicitly
requests a clean-base replacement patch. Native verifier infrastructure,
provider, checkout, and internal AgentTrace failures are not software
counterexamples.

Repair token, latency, patch-size, outcome, counterexample-source, success, and
regression metrics are stored under `Run.model_parameters.repair_metrics`.
The eight protocol milestones use the existing `TraceEvent` table. This is a
minimal Phase 7 trace; the full trace/export system remains Phase 9 work.
