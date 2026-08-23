# External public repository workflow

AgentTrace supports public HTTPS Git repositories in addition to the curated
benchmark. Registration is metadata-only: AgentTrace resolves and records the
full HEAD commit, creates a managed bare clone, inspects a bounded Git tree, and
detects Python project metadata without checking out or running repository code.
URLs containing credentials, query strings, fragments, local hosts, or private
literal IP addresses are rejected.

Native Windows verification is not a security sandbox. Never confirm trust for
a repository you are unwilling to execute locally. Repository code can run only
after an explicit trust acknowledgement, and then only from disposable Git
workspaces through the restricted verifier with a temporary virtual environment,
sanitized process environment, bounded output, and hard timeouts. The original
managed bare clone is not modified or executed.

## API sequence

Start the backend, then use the following local HTTP sequence.

1. Register a public repository. This does not grant execution trust.

   ```http
   POST /repositories/external
   Content-Type: application/json

   {
     "repository_url": "https://github.com/OWNER/REPOSITORY.git"
   }
   ```

2. Inspect the returned metadata, obtain the user's explicit trust confirmation,
   and create the task with that acknowledgement.

   ```http
   POST /tasks/external
   Content-Type: application/json

   {
     "repository_id": "external-...",
     "title": "Repair the empty-input behavior",
     "description": "The parser should return an empty result for empty input.",
     "task_category": "bug_fix",
     "test_command": "python -m pytest -q",
     "allowed_paths": ["src/"],
     "forbidden_paths": ["tests/"],
     "trusted_execution_acknowledged": true
   }
   ```

   Trust can instead be managed separately, before task creation, with:

   ```http
   PATCH /repositories/{repository_id}/trust
   Content-Type: application/json

   {
     "trusted_for_local_execution": true,
     "acknowledgement": true
   }
   ```

3. Run the task through the shared configuration interface.

   ```http
   POST /runs
   Content-Type: application/json

   {
     "task_id": "external-...",
     "configuration_id": "D",
     "model": "gemini-2.5-flash"
   }
   ```

Use `GET /repositories`, `GET /tasks`, and `GET /runs/{run_id}` to inspect the
stored state and trace-backed outcome.

## Evidence and verification boundaries

An external task has no known-correct patch, AgentTrace hidden tests, mutation
qualification, or ground-truth difficulty unless it is later curated into the
benchmark. Hypothesis and CrossHair run only when an evaluator-owned profile is
explicitly configured. SBFL evidence is supplied only when a compatible
persisted spectrum exists. Configuration D records unavailable techniques and
continues with the applicable tool, verification, and single-repair behavior.

AgentTrace does not automatically install dependencies from an external
repository. Verification uses the frozen AgentTrace environment; dependency
requirements discovered during registration are metadata for controlled setup
and review, not authorization to execute setup scripts or package installers.
