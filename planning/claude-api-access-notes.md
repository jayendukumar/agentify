# LLM Provider Notes: Claude Account vs. Claude API, and Provider Choice

## Decision (2026-09-17)

**Chosen for now: OpenRouter, routing to Qwen3.7 Flash (`qwen/qwen3.7-flash`).**
Selected for cost -- roughly 1/30th the price of Claude Haiku 4.5 (the
cheapest Claude model) while still supporting the vision, tool-calling, and
long-context features this project's ingestion/BPMN/blueprint pipeline
needs (see the market scan below). Revisit this if:
- Real (non-synthetic/non-test) business process documents are ingested and
  the data-handling implications of an Alibaba Cloud-hosted model become a
  blocker (see US10.5's note in `planning/epics/10-nonfunctional.md`).
- Quality on the harder reasoning steps (BPMN structuring, agent-automation
  evaluation) turns out not to hold up at this price point once real usage
  is tested -- in which case, consider the "pragmatic middle ground" noted
  in the market scan below (cheap model for volume, stronger model for the
  hard steps).
- OpenRouter itself becomes a reliability/cost concern at scale.

## Data privacy decision (2026-09-19, Epic 10, US10.5)

**Decision: do not upload real, sensitive business process documents
against the current default provider (OpenRouter -> Qwen3.7 Flash,
Alibaba Cloud-hosted) without revisiting this.** Every document ingested
by this project so far (HR onboarding, order fulfillment, etc., used
throughout development and testing) has been a synthetic/test fixture,
not a real company's actual process documentation -- this decision
exists because that will not always be true, not because it already
happened.

Why this can't just default to "swap providers": the whole reason
Qwen3.7 Flash was chosen (see "Decision" above) is a ~30-80x cost
advantage over every Western-hosted alternative capable of this
project's vision/tool-calling/structured-output needs, and that
advantage is exactly what a cost-sensitive default should optimize for
when the data isn't sensitive. Forcing every user onto a pricier
Western-hosted model by default would be over-correcting for a risk that
doesn't exist for most documents this tool will actually process (test
fixtures, synthetic examples, already-public process documentation).

**Mitigation path, not yet built:** the backend's LLM integration layer
(`app/llm/`, US9.3) already sits behind a provider-agnostic interface --
swapping the model for a specific ingestion run only requires changing
`LLM_MODEL`/`LLM_BASE_URL` (see the "pragmatic middle ground" market-scan
note below for a Western-hosted alternative), no code change. What's
*not* built yet: any UI/config mechanism to select a different provider
per-document or per-process based on sensitivity, or an automated warning
at upload time beyond the static notice now shown on the upload page
(`frontend/src/pages/ProcessDetailPage.tsx`) -- today this is a
documented policy a user must apply themselves by setting
`LLM_MODEL`/`LLM_BASE_URL` for the whole deployment before ingesting
sensitive material, not a per-document runtime choice. Worth building
if/when this tool is actually used against real confidential process
documentation, not before.

Everything below this point is background: what a Claude.ai subscription
does and doesn't cover, and the full provider comparison this decision was
made from. Kept for reference in case the provider choice is revisited.

## Claude Account vs. Claude API — Is This Separate Spend?

**Short answer: yes, separate.** A claude.ai subscription (Free / Pro / Max /
Team / Enterprise) and the **Anthropic API** (console.anthropic.com) are two
different products with two different billing relationships. Building this
application requires the API, which is **not** included in a claude.ai chat
subscription. (This background is retained even though the current provider
choice above is not Anthropic, in case that changes.)

## The two products

| | claude.ai (Pro/Team/Enterprise) | Anthropic API |
|---|---|---|
| What it is | Chat web/desktop/mobile app, Claude Code usage tied to a subscription plan, Projects, Artifacts | Programmatic `POST /v1/messages` access for building your own application |
| Billing | Flat monthly/annual subscription | Pay-as-you-go, billed per token, via a separate Anthropic Console account (or Amazon Bedrock / Google Vertex AI / Microsoft Foundry, each with their own billing) |
| Where it's managed | claude.ai account settings | console.anthropic.com — requires its own account, payment method, and API keys |
| Does one include the other? | No. A claude.ai subscription does not grant API credits, and an API account does not include claude.ai Pro features. | Same |

## What this means for this project

To power ingestion → BPMN generation → chat editing → blueprint evaluation,
this app needs:

1. An **Anthropic Console account** (console.anthropic.com) — separate signup
   from claude.ai, even if using the same email/org.
2. A **billing method on file** there (the API is pay-as-you-go; there is a
   free trial credit for new orgs, but production usage is metered).
3. **API key(s)** generated from the Console, injected into the backend via
   environment variables (`ANTHROPIC_API_KEY`) — never committed to source
   control (see Epic 9 / US9.10).
4. A cost-tracking plan once usage grows — see Epic 10 / US10.3 (log
   `usage.input_tokens` / `usage.output_tokens` per Claude call) and set
   budgets/rate limits in the Console.

## Alternative billing paths (if useful later)

If the organization already has committed spend on a cloud provider, Claude
is also available — under that provider's own billing, not Anthropic's —
via:
- **Amazon Bedrock**
- **Google Cloud Vertex AI**
- **Microsoft Foundry**

These are relevant if there's a reason to route billing through an existing
cloud contract; for local development, a direct Anthropic API key is the
simplest path.

## Reference pricing (current at time of writing; recommended default model)

| Model | Model ID | Input $/1M tokens | Output $/1M tokens |
|---|---|---|---|
| Claude Opus 5 (recommended default) | `claude-opus-5` | $5.00 | $25.00 |
| Claude Sonnet 5 (cheaper, still strong) | `claude-sonnet-5` | $2.00 | $10.00 |
| Claude Haiku 4.5 (cheapest, fast) | `claude-haiku-4-5` | $1.00 | $5.00 |

Given the volume of document ingestion and per-node blueprint evaluation this
product does, it is worth revisiting model choice per call site (e.g., Haiku
for cheap classification-style calls like per-node automation scoring,
Opus/Sonnet for the harder BPMN-structuring and extraction calls) once real
usage patterns are known — see Epic 10 for cost tracking, and use the
`claude-api` skill's `cost-optimize` workflow once the app has real traffic.

## Action items

- [ ] Create/confirm an Anthropic Console account for this project (can be
      the same email as claude.ai — jayendu.kumar@gmail.com — but is a
      separate signup and separate billing).
- [ ] Add a payment method / confirm trial credit balance.
- [ ] Generate a dev API key, store it in a local `.env` (git-ignored).
- [ ] Decide whether ingestion/blueprint evaluation should default to
      Opus 5, or split by call type for cost (see above).

## Market-wide cheapest-option scan (Sep 2026)

Looked beyond Anthropic at what the broader LLM API market currently charges,
since cost was raised as a concern. Prices are $/1M tokens, input / output.

| Model | Provider | Input | Output | Vision | Tool calling | Context | Notes |
|---|---|---|---|---|---|---|---|
| Ling 3.0 Flash | inclusionAI (China) | $0.02 | $0.06 | unclear | unclear | -- | Rock-bottom price, but essentially unvetted for this use case -- no clear docs on vision/tool-use support found |
| Qwen3.7 Flash | Alibaba Cloud | $0.03 | $0.13 | Yes | Yes | 1M | Cheapest option that is actually feature-complete for this project (vision + tool calling + structured output + long context) |
| DeepSeek V4-Flash | DeepSeek (China) | $0.15 off-peak / $0.30 peak | $0.60 / $1.20 | limited | Yes | 1M | Aggressive peak/off-peak pricing (01:00-04:00 & 06:00-10:00 UTC weekdays are "peak"); cache-hit input as low as $0.003-0.006 |
| Amazon Nova Micro | AWS Bedrock | $0.035 | $0.14 | No | Yes | ~128K | Text-only -- would not cover image ingestion (US1.5) on its own |
| Amazon Nova Lite | AWS Bedrock | $0.06 | $0.24 | Yes | Yes | ~300K | Cheapest US-hosted multimodal option found |
| Gemini 2.5 Flash-Lite | Google | $0.10 | $0.40 | Yes | Yes | 1M | Google is retiring this Oct 16, 2026 -- not a safe long-term pick |
| Gemini 3.5 Flash-Lite | Google | $0.30 | $2.50 | Yes | Yes | 1M | Its practical successor once 2.5 Flash-Lite retires |
| GPT-5 Nano | OpenAI | $0.05 | $0.40 | Yes | Yes | 400K | Cheapest mainstream-provider option; positioned by OpenAI for summarization/classification, not heavy reasoning |
| GPT-5 Mini | OpenAI | $0.25 | $2.00 | Yes | Yes | 400K | More headroom than Nano for the harder steps (BPMN structuring, agent evaluation) |
| Claude Haiku 4.5 | Anthropic | $1.00 | $5.00 | Yes | Yes | 200K | For reference -- cheapest current Claude model, already documented above |

### Reading this for this project

- **Absolute cheapest in the market:** Ling 3.0 Flash / Qwen3.7 Flash
  (Alibaba), around $0.02-0.03 input / $0.06-0.13 output per 1M tokens --
  roughly 40-80x cheaper than Claude Opus 5 and ~30x cheaper than Claude
  Haiku 4.5. Qwen3.7 Flash is the one of the two with confirmed vision,
  tool-calling, structured-output, and 1M context support, which is what
  this app's ingestion/BPMN/blueprint pipeline actually needs -- Ling 3.0
  Flash's capability set was not clearly documented in available sources.
- **Cheapest with a Western/US data-handling story:** Amazon Nova Lite
  ($0.06/$0.24, multimodal) or GPT-5 Nano ($0.05/$0.40, multimodal but
  positioned for lighter tasks) -- both well under Claude Haiku 4.5.
- **Caveat that matters more than price here:** this product ingests
  business process documentation, which may be sensitive/confidential
  (Epic 10, US10.5). The cheapest options (Ling 3.0 Flash, Qwen3.7 Flash,
  DeepSeek) are hosted by Chinese providers with data-handling and privacy
  terms that differ materially from Anthropic/OpenAI/Google/AWS -- that is a
  real factor to weigh against the roughly 95% cost saving, not just a
  checkbox.
- **Tooling caveat:** this repo's Claude Code setup already has a
  purpose-built `claude-api` skill (SDK patterns, model IDs, thinking/effort
  config, etc.) for Anthropic. Switching providers means losing that
  scaffolding and hand-rolling the integration layer (Epic 9, US9.3) against
  a different SDK/API shape.
- **A pragmatic middle ground, if cost is the driver:** keep Claude (or
  another trusted provider) for the harder reasoning steps (BPMN
  structuring, agent-automation evaluation) and route the cheap, high-volume
  calls (e.g. per-page OCR/extraction passes, simple classification) to a
  cheaper model -- the same cost-optimization lever the `claude-api` skill's
  `cost-optimize` workflow already recommends within the Claude model
  family, just extended across providers. Decide this once real usage
  volume is known rather than pre-emptively.

Sources: [CloudZero LLM pricing comparison](https://www.cloudzero.com/blog/llm-api-pricing-comparison/), [Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing), [OpenAI API pricing](https://developers.openai.com/api/docs/pricing), [DeepSeek API pricing (Morph)](https://www.morphllm.com/deepseek-api), [Qwen3.7 Flash on OpenRouter](https://openrouter.ai/qwen/qwen3.7-flash), [Amazon Nova pricing (devtk.ai)](https://devtk.ai/en/models/nova-lite/), [Cheapest vision LLM API 2026 (clawrouters)](https://www.clawrouters.com/blog/cheapest-vision-multimodal-llm-api-2026)
