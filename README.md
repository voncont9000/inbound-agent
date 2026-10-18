# inbound-agent
A personalised, vertical-agnostic AI agent that takes customer inquiries via phone (speech-to-text / text-to-speech), draft-only Gmail, and WhatsApp.

## Docs
- [Product requirements (PRD)](docs/PRD.md)
- [Customer context graph](docs/context-graph.md): research and design for linking interactions across channels
- [M1 architecture and agent seams](docs/m1-architecture.md)
- [Voice pipeline (M0 architecture)](docs/voice-pipeline.md)
- [Production deployment](docs/deployment.md): single-server Hetzner operations runbook
- [Text channels: Gmail and WhatsApp](docs/text-channels.md)
- [Google Calendar booking](docs/booking.md): appointment types, Calendar connection, and booking behavior
- [After-conversation actions](docs/actions.md)
- [Website-based assistant setup](docs/assistant-setup.md): draft, review, and acceptance flow
- [Fonio teardown](docs/fonio-teardown.md): reverse-engineering notes on the reference product, made with [REA](https://github.com/morluto/rea)

## Local development

See [M1 architecture](docs/m1-architecture.md#local-run) for the Python, Postgres, and web setup.

Run the background job worker separately with
`uv run --directory backend python -m inbound_api.worker`.

The WhatsApp and Gmail adapters, fake-mode simulators, settings, reply-window and
draft-only behavior, and current channel limitations are documented in
[Text channels](docs/text-channels.md).
After-conversation providers, templates, webhook signing and delivery guarantees are
documented in [After-conversation actions](docs/actions.md).
