# Axyntro wiki

Axyntro is an agentic solution generator. It ingests business-process
documentation, builds and refines a BPMN model, evaluates every step for
AI-agent automation, generates governed agent definitions, and proves
them in a digital twin. That proof works one agent at a time, at volume
against ground truth, and as a whole process measured against how the
process runs today.

| Page | What's in it |
|---|---|
| [Architecture](Architecture.md) | System context, containers, the pipeline, run and process twin sequence diagrams, state machines, cross-cutting concerns |
| [Data Model](Data-Model.md) | ER diagrams by domain, lifecycles, every table and column |
| [Prompts and LLM](Prompts-and-LLM.md) | How model calls are made and governed; every prompt, from the code |
| [API Reference](API-Reference.md) | Every endpoint and who may call it |
| [Process Twin](Process-Twin.md) | The whole-process digital twin (Epic 36): how it runs and how savings are computed |
| [Code Health](Code-Health.md) | Complexity, duplication, hotspots, coupling, dead code, growth, mutation score |
| [Critical Review](Critical-Review.md) | Where the architecture and product are weak, by severity |
| [Keeping Docs Current](Keeping-Docs-Current.md) | How the wiki stays true: generated blocks, checks, hooks |

Elsewhere in the repository:

- **Planning:** [`planning/00-index.md`](../../planning/00-index.md) (epics and
  stories) and [`planning/decision-log.md`](../../planning/decision-log.md)
  (why things are the way they are).
- **Demos:** `demo-deck/`. Includes the Meridian case study (one agent,
  Epics 27-35), the Invoice to Pay process twin (Epic 36), and the
  enterprise-readiness deck.
- **Running it:** `start.ps1` (`-Demo -ResetDemo -SeedProcessTwin` for the
  process twin demo).
