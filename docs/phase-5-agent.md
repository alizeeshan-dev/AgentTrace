# Phase 5: constrained model agent and baselines

Phase 5 adds provider-neutral, structured model interaction and the first two
experimental configurations. It does not run verification and never returns
test, hidden-test, mutation, or SBFL evidence to a model.

## Provider boundary

`app.agent.provider.ModelProvider` accepts a typed `ModelRequest` and returns a
typed `ModelResponse`. A response contains exactly one validated action plus
the model identifier and parameters, token counts, provider-reported latency,
request identifier, and finish reason when available. Provider failures cross
the boundary only as a controlled `ModelProviderError`. The scripted
`FakeModelProvider` supports deterministic local tests without an SDK, network
access, or credentials.

The built-in real adapter is `GeminiModelProvider`, implemented against the
Gemini Developer API through Google's Gen AI Python SDK. It represents every
repository action and patch submission as a required provider function call, validates its arguments with
the existing Pydantic schemas, and returns the same provider-neutral
`ModelResponse`. See `docs/gemini-provider.md` for local credential and model
configuration.

Actions are either a single approved repository tool call or a terminal
`submit_patch`. Concise observable explanation fields are permitted. Arbitrary
extra fields, including purported hidden chain-of-thought, are rejected.

## Configuration A

The direct baseline builds a sorted, bounded repository snapshot from the
recorded base commit. The snapshot includes only agent-readable workspace
content, is content-hashed, and is stable for the same task, commit, and
budgets. One model response is requested with no tools. A tool request is a
terminal protocol failure; a submitted patch is validated, stored, applied in
the disposable checkout, and the run stops.

## Configuration B

The tool agent initially receives only the task and non-secret repository
metadata. On each turn it may request `list_tree`, `read_file`, or literal
`search_code`. The orchestrator validates and bounds the result before adding
it to the conversation. The loop ends at the first `submit_patch`, provider
failure, or explicit budget exhaustion.

Neither configuration receives hidden-test paths, the known-correct patch,
SBFL output, verification feedback, or a repair opportunity.

## Safety and accounting

Phase 2 path policy remains the filesystem enforcement point. It protects
canonical repository boundaries, evaluator paths, `.git`, symlink escapes,
write allowlists, and forbidden paths. Phase 5 additionally bounds model
turns, tool calls, scanned/read files, exposed files and content, individual
files, tree/search results, patch bytes and lines, changed files, and wall
clock time.

Candidate patches must be Git-style unified text diffs. Binary, rename, copy,
mode, symlink, submodule, hidden-test, `.git`, traversal, test modification
without explicit permission, and over-budget changes are rejected. Git checks
clean applicability before the trusted orchestrator applies the hash-bound
candidate. No patch-application capability is exposed as a model tool.

The database records the pre-verification run and its candidate. Content-
addressed model and patch artifacts preserve safe prompts, observable actions,
bounded tool results, provider metadata, rationale, outcome, and hashes. A
successful Phase 5 status means only that the candidate was accepted and
applied to the disposable checkout; `final_resolution` remains unknown until
Phase 6 verification.
