# Quality and observability

## Tracing

Set `OTEL_EXPORTER_OTLP_ENDPOINT` to enable tracing; it is a no-op when unset. For Langfuse EU,
use `https://cloud.langfuse.com/api/public/otel` with `LANGFUSE_PUBLIC_KEY` and
`LANGFUSE_SECRET_KEY`. `LANGFUSE_HOST` can be set to `https://cloud.langfuse.com` for conversation
links in the admin view.

Pipecat exports conversation → turn → STT/LLM/TTS spans. Conversation spans include `channel`,
`tenant_id`, `assistant_id`, and `conversation_id`; `conversation.id` is the external call ID.
Email addresses and phone-like numbers in exported span attributes are redacted on a best-effort
basis; spelled-out words are not detected. The conversation trace ID is stored in
`conversations.metrics["trace_id"]`. When `LANGFUSE_HOST` is set, the backend builds
`trace_url = f"{LANGFUSE_HOST}/trace/{trace_id}"` for the admin view.
