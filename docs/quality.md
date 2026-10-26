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

## Conversation quality review and evaluations

Quality scoring is shared across conversation channels and uses vertical-neutral assistant
instructions. Platform administrators are configured through the comma-separated
`PLATFORM_ADMIN_EMAILS` setting. Email matching is case-insensitive; requests from other users
receive `404 Not Found` for `/admin` routes so the administrative surface is not disclosed.

### Scoring

Voice reports and completed text extraction enqueue a durable `conversation_score` job in the
same tenant-scoped transaction as their conversation results. A queued scoring job for the same
conversation is reused rather than duplicated. Test conversations are scored too and retain the
`is_test` flag in administrative listings.

The worker uses `GOOGLE_API_KEY` and `QUALITY_JUDGE_MODEL` (default
`gemini-3.5-flash-lite`) to apply rubric version `v2`. Rubric v2 clarifies that platform notices
are not assistant mistakes: an automatically added recording notice near the start of a call
(for example, “This call may be recorded”) never counts against any check. The rubric covers six
checks:

- `answered_question`
- `collected_required_details`
- `language_consistent`
- `no_unsupported_promises`
- `ended_properly`
- `followed_instructions`

Each check records `passed` (`true`, `false`, or `null`) and an optional note. The overall score is
an integer from 1 to 5, and up to five concrete issue phrases are retained. A conversation needs
review when its overall score is 3 or lower, any check failed, or its maximum voice response time
is greater than 3 seconds.

Voice objective metrics are recorded when latency data is available: `response_p50_s`,
`response_p95_s`, `max_response_s`, `interruptions`, `turns`, `call_duration_s`, and `tts_chars`.
These are derived from the conversation's voice latency summary, turn records, duration,
interruption count, and TTS usage. Text conversations have `objective: null`.

### Platform administration

Every `/admin` route requires an authenticated platform-admin email. Platform-wide overview and
conversation-list reads use the `admin_conversation_facts` and `admin_conversation_list`
security-definer functions. They are owned by the non-login, non-bypass-RLS
`inbound_platform_reader` role, which has read-only access and explicit read policies on the
relevant tenant tables. A single-conversation detail or mutation first resolves its tenant with
`conversation_tenant(uuid)`, then uses the normal `tenant_session` and tenant RLS policies.

The API provides:

- `GET /admin/overview` for conversation counts, scored/review counts, score and response-latency
  summaries, daily totals, and tenant totals.
- `GET /admin/conversations` for filtered, cursor-paginated cross-tenant listings.
- `GET /admin/conversations/{conversation_id}` for transcript, merged intake, score, and optional
  trace link.
- `POST /admin/conversations/{conversation_id}/rescore` to queue a score.
- `POST /admin/conversations/{conversation_id}/eval-case` to create an evaluation case from a
  conversation.
- `GET /admin/eval-cases`, `GET /admin/eval-runs`, and
  `GET /admin/eval-runs/{eval_run_id}` to inspect evaluation data.

`eval_cases` stores reusable caller utterances and check expectations; `eval_runs` stores run
metadata, aggregate counts, and per-case results. The internal
`POST /internal/eval-runs` endpoint accepts completed evaluation results and requires the voice
service token. There is intentionally no internal eval-case read endpoint in this workstream.

### Configuration

Set these values for both the API and worker:

| Variable | Default / format | Purpose |
|---|---|---|
| `PLATFORM_ADMIN_EMAILS` | Empty; comma-separated emails | Accounts allowed to open `/admin` |
| `QUALITY_JUDGE_MODEL` | `gemini-3.5-flash-lite` | Model used for conversation judgement |
| `LANGFUSE_HOST` | Empty | Optional base URL used to construct trace links |
| `GOOGLE_API_KEY` | Required for scoring | API key used by the scoring worker |

See the [deployment runbook](deployment.md) for production environment configuration.
