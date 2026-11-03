# JobPilot documentation

Start with the project [README](../README.md) for setup and a product overview. These documents go deeper.

| Document | Read it when you want to... |
| --- | --- |
| [Architecture](architecture.md) | Understand the layers, request flow, data model, and design tradeoffs |
| [HTTP API](api.md) | Integrate with the JSON API or understand the web routes |
| [Safety and memory](safety.md) | Review the threat model, permissions, claim verification, and memory policy |
| [Evaluation](evaluation.md) | Reproduce the benchmarks and understand the metrics and quality gates |
| [Development](development.md) | Add a source, tool, page, or MCP server; run tests; troubleshoot |

## The short version

- **Local-first.** No API key, no external database, no Node build step.
- **MCP-shaped.** Six typed servers share one service layer with the UI and agent.
- **Human-gated.** External actions always pause for approval; nothing is auto-submitted.
- **Verified generation.** Every tailored claim is checked against profile evidence.
- **Observable.** Each run writes a JSONL trace and a database record.
- **Measured.** An offline suite covers ranking, grounding, planning, memory, and injection defense.

## Repository map

```
src/jobpilot/
├── core/        settings, models, SQLite
├── harness/     loop, context, gateway, permissions, guardrails, tracing
├── memory/      storage, write/read policy, summarization
├── pipelines/   extraction, dedupe, embeddings
├── sources/     adapters and fallback registry
├── services/    business logic and the typed tool registry
├── servers/     MCP stdio servers
└── ui/          Flask app, CLI, templates, static assets
eval/            dataset, metrics, baselines, report
tests/           isolated pytest suite
```
