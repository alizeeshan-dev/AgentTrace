# Evidence-based run reports

AgentTrace can materialize a deterministic report for one completed benchmark
or trusted external-repository run. The report follows the stored sequence from
task and investigation through submitted patches, verification,
counterexamples, optional bounded repair, and final outcome. It does not invoke
an LLM and is not a whole-repository code review or health score.

## Evidence boundary

Reports use only persisted `Repository`, `Task`, `Run`, `TraceEvent`,
`FaultLocalizationResult`, `PatchArtifact`, `VerificationResult`,
`Counterexample`, and `BenchmarkQuality` records plus the hashes of referenced
artifacts. Observed evidence and interpretation remain separate: every
assessment dimension includes its basis, while missing evidence is labelled
Not Assessed, Not Configured, Not Available, Unavailable, or Not Used.
The same trace redaction boundary is applied before structured or Markdown
report persistence; hidden-test content, credentials, and oversized text are
not copied directly into report artifacts.

The report exposes separate dimensions for final resolution, configured
verification, mutation-test oracle strength, regression evidence, transparent
patch scope, fault-localization availability, repair requirement, and advisory
static analysis. It deliberately does not calculate an overall numerical
quality score.

Patch scope uses a visible descriptive rule: Focused means at most two changed
files and at most 50 added-plus-removed lines; Moderate means at most five files
and at most 200 changed lines; larger patches are Broad. The underlying file and
line counts remain in the report.

## Benchmark and external reports

Benchmark reports can include mutation qualification, hidden-test outcomes,
difficulty, and configured property or symbolic evidence. External reports
identify the public repository URL and exact commit, but explicitly disclose
the absence of benchmark ground truth, known-correct patches, evaluator-owned
hidden tests, and mutation qualification where those were not configured.
Passing all available external checks must not be presented as equivalent to
passing the stronger benchmark oracle.

## API

Generate the report once the run has a terminal status:

```http
POST /runs/{run_id}/report
```

The first request returns HTTP 201 with report metadata. Repeating it is
idempotent and returns HTTP 200 with the same report and artifact identity.
Active runs are rejected with HTTP 409.

Retrieve structured JSON:

```http
GET /runs/{run_id}/report
```

Retrieve human-readable Markdown:

```http
GET /runs/{run_id}/report/markdown
```

Markdown is stored through AgentTrace's content-addressed artifact store. The
database record preserves the report ID, generation version, timestamp,
evidence snapshot hash, artifact reference, Markdown hash, and source artifact
references. Repeated generation does not create duplicate report records or
artifact content.
