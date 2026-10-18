# inbound-agent
A personalised AI agent that takes customer inquiries via phone (speech-to-text / text-to-speech), email (Gmail), and WhatsApp.

## Docs
- [Product requirements (PRD)](docs/PRD.md)
- [Customer context graph](docs/context-graph.md): research and design for linking interactions across channels
- [M1 architecture and agent seams](docs/m1-architecture.md)
- [Voice pipeline (M0 architecture)](docs/voice-pipeline.md)
- [Text channels and WhatsApp](docs/text-channels.md)
- [Fonio teardown](docs/fonio-teardown.md): reverse-engineering notes on the reference product, made with [REA](https://github.com/morluto/rea)

## Local development

See [M1 architecture](docs/m1-architecture.md#local-run) for the Python, Postgres, and web setup.

Run the background job worker separately with
`uv run --directory backend python -m inbound_api.worker`.

The WhatsApp adapter, fake-mode simulator, settings, 24-hour reply-window behavior, and
current media/template limitations are documented in [Text channels](docs/text-channels.md).
