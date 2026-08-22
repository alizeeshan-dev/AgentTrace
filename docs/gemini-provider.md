# Gemini model provider

AgentTrace includes one built-in real provider adapter: the Gemini Developer
API through Google's `google-genai` Python SDK. The adapter implements the
existing `ModelProvider` protocol; Configurations A, B, C, and D do not import
or depend on the SDK directly.

Every model response must be exactly one function call representing
`list_tree`, `read_file`, `search_code`, or `submit_patch`. AgentTrace validates
the function arguments with its existing Pydantic action schemas. Gemini never
executes repository tools or applies patches; the existing constrained
orchestrator remains the only execution authority.

## Credential setup

From the repository root, create the ignored local environment file:

```powershell
Copy-Item -LiteralPath .env.example -Destination .env
```

Open `D:\AgentTrace\.env` and replace this line without adding quotes:

```dotenv
GEMINI_API_KEY=replace_with_your_real_key
```

`Settings` reads `.env` with Pydantic Settings. The credential is held as a
`SecretStr`, is not part of `ModelConfiguration`, and is never written to
experiment YAML, model requests, traces, logs, artifacts, or benchmark
subprocess environments. `.env` and `.env.*` are ignored by Git, while
`.env.example` remains tracked.

## Model and request configuration

The one-task example is `experiments/gemini-smoke.yaml`. Select the provider
and model under `model`:

```yaml
model:
  provider: gemini
  model: gemini-3.7-flash
  model_version: null
  api_key_env: GEMINI_API_KEY
  request_timeout_seconds: 120.0
  max_retries: 0
  temperature: 0.0
  parameters:
    max_output_tokens: 12000
```

Change `model` to another Gemini Developer API model identifier only before
freezing an experiment. AgentTrace records the provider-returned model version
on each response where available and does not invent one in configuration.

Supported provider-specific `parameters` are `max_output_tokens`, `seed`,
`stop_sequences`, `thinking_config`, `top_k`, `top_p`, and `temperature`.
Authentication fields are rejected from model parameters.

## Token and cost accounting

Gemini-reported prompt/tool-input, candidate/thinking-output, and total token
counts are normalized into AgentTrace's input, output, and total usage fields.
Optional estimated cost uses the experiment YAML `cost` section. The one-task smoke configuration records
zero rates because it is intended for the Gemini Developer API free tier.
Before research collection, confirm the tier and freeze both rates and the
pricing source. Pricing remains outside the provider adapter so it can change
without changing request behavior.

## One real-model run

The benchmark task must already be qualified in the selected state directory.
Then run exactly one Configuration A task with:

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m app.experiments.cli `
  --config experiments/gemini-smoke.yaml `
  --benchmark-root benchmark `
  --state-dir .agenttrace
```

No `--provider-factory` option is needed when `model.provider` is `gemini`.
Missing credentials, authentication failure, rate limiting, timeout, malformed
actions, unavailable models, and provider failures cross the provider boundary
as controlled AgentTrace errors.
