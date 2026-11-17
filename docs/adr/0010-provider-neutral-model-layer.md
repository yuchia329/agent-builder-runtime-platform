# 0010. Provider-neutral model layer

- Status: Accepted
- Date: 2026-11-16
- Supersedes: "workers run on Amazon Bedrock" as the only model provider (AGENTS.md, README,
  architecture); the Bedrock-only adapter plan in [ADR 0009](0009-worker-owns-agent-loop.md)

## Context

The design assumed every model call goes to Amazon Bedrock. In practice the cheapest and simplest
option for development is an API key the owner already pays for (MiniMax), and the same model is
sold by several hosts (MiniMax, OpenRouter, Together AI, Bedrock) at different prices. We also want
to run on OpenAI, Gemini, or a self-hosted server (vLLM, Ollama) without touching the agent loop.

ADR 0009 already puts the model behind a `Model` port, so the loop does not care who answers. What
is missing: how a run picks a provider, how many adapters that takes, and where provider keys live.

Options considered:

- **One adapter per vendor.** Clear, but most vendors speak the same wire format, so most adapters
  would be copies.
- **A multi-provider library (LiteLLM or similar) behind one adapter.** Many providers at once, but
  a large dependency in the process that holds the keys, its own message format next to ours
  (ADR 0009 says we own it), and its retry and error behavior instead of ours.
- **One adapter per wire protocol, plus a router.** Two protocols cover every provider we name:
  the OpenAI Chat Completions format (OpenAI, MiniMax, OpenRouter, Together AI, Gemini's
  OpenAI-compatible endpoint, vLLM, Ollama, llama.cpp) and the Bedrock Converse API.

## Decision

- **The spec names provider and model:** `model: <provider>/<model-id>`, split at the first `/`,
  for example `minimax/MiniMax-M3`, `openrouter/minimax/minimax-m3`, `bedrock/<Bedrock model or
  inference profile id>`, `local/llama3.1`. `stub/...` is the built-in stub. The spec still says
  nothing about URLs or keys.
- **The worker config defines providers:** `MODEL_PROVIDERS=minimax,local` lists them; for each
  name `<NAME>`, `MODEL_<NAME>_PROTOCOL` (`openai` or `bedrock`), and for `openai` also
  `MODEL_<NAME>_BASE_URL` and `MODEL_<NAME>_API_KEY` (optional for a local server). Bedrock uses
  the worker's AWS credentials and `AWS_REGION`. Adding a provider that speaks a supported
  protocol is config, not code.
- **Adapters, one per protocol:** `adapters/model_openai_compat.py` and `adapters/model_bedrock.py`.
  A native adapter for one vendor is added only when its compatible endpoint lacks something the
  loop needs; that is a code change with tests, not a new decision.
- **A router chooses per call:** the `Model` port gains the model string
  (`complete(model, system, messages, tools)`); core passes `spec.model` through unchanged.
  `adapters/model_router.py` implements the port, looks up the provider prefix, and forwards. An
  unknown or unconfigured provider raises `ModelError`, so the run ends `FAILED` with a clear
  message.
- **Prices are per `provider/model`:** `core/pricing.py` holds dollars per million input and output
  tokens. `max_usd` is required in every spec, so a run whose model has no price fails before its
  first step instead of running without a dollar limit.
- **Provider keys are platform secrets held by the worker:** `.env` locally (git-ignored),
  Secrets Manager in AWS (M6). They are not tenant credentials, so ADR 0006 is unchanged: tool
  credentials still live only in the gateway. Keys never reach the model, tools, logs or spans.
- **First real provider: MiniMax** through the OpenAI-compatible adapter. The Bedrock adapter
  follows as an optional task.

## Consequences

- Switching provider, or one agent's model, is a spec or env change; the loop and its guardrails
  are the same for every provider.
- Each adapter passes the `Model` contract suite. Providers that share a protocol still differ in
  small ways (tool-call ids, how usage is reported, stop reasons), so the OpenAI-compatible
  adapter gets recorded-response tests per provider we use.
- With a non-AWS provider, prompts and tool results leave AWS. Which providers a deploy configures
  is its owner's choice; a self-hosted server keeps data in-house.
- The worker holds provider keys, so a leaked worker environment exposes model spend (not tenant
  data). Per-run budgets and a spend alert on each provider account limit the damage.
- Bedrock is no longer required for M1, so AWS setup can wait until M6.
- Price tables go stale; keep them next to the adapter's config and check them when adding a model.
