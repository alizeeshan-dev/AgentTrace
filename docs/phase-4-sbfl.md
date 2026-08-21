# Phase 4 spectrum-based fault localization

Phase 4 produces fault-localization evidence before any LLM call. It reuses a
qualified Phase 3 task, its immutable Git bundle, a disposable Phase 2
workspace, content-addressed artifacts, and the existing SQLite research
models.

## Collection and redaction

The collector runs the visible and evaluator-owned hidden pytest suites with
pytest-cov and Coverage.py's per-test dynamic contexts. It records whether each
test passed, failed, or skipped and which allowlisted production lines the test
executed. Coverage is filtered to production paths from the task manifest.

Hidden tests remain outside the disposable repository. Before coverage leaves
the collector, hidden pytest node IDs are replaced by stable opaque hashes.
Temporary Coverage databases and evaluator outcome files are deleted. The
stored raw JSON contains no hidden source, path, test name, assertion, or test
output.

This Phase 4 runner is limited to evaluator-authored benchmark commands in an
independent disposable clone. It is not the later untrusted-patch verifier.
Phase 6 must place arbitrary repository execution behind the specified
network-denied, resource-bounded Docker boundary.

## Spectrum and ranking

For each executable allowlisted source line, AgentTrace records:

- `ef`: failing tests that executed the line;
- `nf`: failing tests that did not execute the line;
- `ep`: passing tests that executed the line.

Skipped tests do not contribute to these counts. Ochiai is implemented as:

```text
ef / sqrt((ef + nf) * (ef + ep))
```

A zero denominator produces `0.0`. Locations sort by descending score, then
repository-relative file, line, and symbol. Ranks are ordinal after this stable
tie break; therefore equal-score lines can have consecutive ranks. This policy
is deterministic and should be retained when comparing Top-K results.

Ranked entries contain file, line, enclosing function/class where available,
Ochiai score, and the raw `ef`/`nf`/`ep` counts. The output is evidence and does
not assert that a ranked location is the real fault.

## Pilot validation

| Task | True-fault rank | Top-1 | Top-5 | Top-10 |
|---|---:|---:|---:|---:|
| `boundary-empty-input` | 1 | yes | yes | yes |
| `transformation-slug-collapse` | 2 | no | yes | yes |
| `validation-business-rule` | 1 | yes | yes | yes |

The transformation task's lines 7 and 8 have equal Ochiai scores. The declared
fault at line 8 receives ordinal rank 2 under the frozen file/line tie break.
Across the three pilots, Top-1 containment is 2/3 and Top-5/Top-10 containment
are both 3/3.

The pre-agent localization run is stored with configuration `sbfl-only` and
model `not-applicable`. Phase 5 must expose only a bounded ranked summary and
must label it as localization evidence; it must not expose the raw coverage
artifact or hidden-test identities.
