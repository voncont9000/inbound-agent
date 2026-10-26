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
`gemini-3.5-flash-lite`) to apply rubric version `v3`. Before judging, the worker labels known
recording notices as `platform` transcript rows, separating them from assistant speech. The
notices include the default and locale-specific recording notices, plus the assistant's configured
consent message when its configuration parses. Lines marked `platform` are automatic system
notices, not said by the assistant, and never count against any check. The rubric covers six checks:

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

### Human review

`needs_review` is the judge's score result. The unresolved review queue and the overview's
needs-review totals count scores where `needs_review` is true and `reviewed_at` is null. Marking a
conversation reviewed records the administrator and timestamp without changing the judge result;
reopening it clears both review fields. A new score, including a rescore, clears the review state so
the new result can be reviewed independently.

The admin review list supports marking one or multiple conversations reviewed. The detail page
supports both marking reviewed and reopening. `POST /admin/conversations/review` accepts 1–100
conversation IDs and a `reviewed` boolean; IDs without a conversation or score are ignored and do
not contribute to the `updated` count.

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
- `POST /admin/conversations/review` to mark scored conversations reviewed or reopen them.
- `POST /admin/conversations/{conversation_id}/rescore` to queue a score.
- `POST /admin/conversations/{conversation_id}/eval-case` to create an evaluation case from a
  conversation.
- `GET /admin/eval-cases`, `GET /admin/eval-runs`, and
  `GET /admin/eval-runs/{eval_run_id}` to inspect evaluation data.

`eval_cases` stores reusable caller utterances and check expectations; `eval_runs` stores run
metadata, aggregate counts, and per-case results. The internal `GET /internal/eval-cases` endpoint
returns enabled cases to the phone runner, and `POST /internal/eval-runs` accepts completed
evaluation results. Both endpoints require the voice service token.

### Regression evals

Regression evals run enabled scripted cases through the phone harness, score the resulting
transcript and intake data, and store a summary for comparison over time. Provider calls use the
selected production STT, TTS, and LLM providers, so a run can incur provider usage charges.
Production sets `RUN_EVALS_AFTER_DEPLOY=1`, so evals run after each deployment's health check;
failures warn without failing the deployment. Cases run one at a time. Caller-audio requests are
throttled to respect Speechify's 1 request-per-second plan limit, and synthesized utterances are
cached on disk. Execution failures are also posted as failed eval runs with the error on each case.

The `eval_cases` and `eval_runs` tables are owned by the conversation-quality migration (`0180`).
Cases store a name, optional source conversation and assistant IDs, caller utterances, named checks,
and an enabled flag. Runs store timestamps, trigger, optional Git SHA, summary counts, and
per-case results. The internal service contract is:

- `GET /internal/eval-cases` returns enabled cases and a resolved assistant route when possible.
  Route lookup uses `conversation_tenant(uuid)` on the case's source conversation; when no source
  conversation exists, the route is `null`. A case with an `assistant_id` but no source
  conversation is reported by the runner as the pre-run failure `assistant not found`.
- `POST /internal/eval-runs` stores a run and returns its `eval_run_id`.

Both endpoints require the `X-Service-Token` voice service token. Case creation is an admin
workflow: retain caller messages as `caller_utterances` in their original order, and create
`intake_checks` from final intake values using regex-escaped literal patterns. Cases without an
assistant can use the harness's default test route.

Inside the production phone container:

```sh
docker exec -it inbound-phone-1 python -m eval.run_cases
docker exec -it inbound-phone-1 python -m eval.run_cases --list
docker exec -it inbound-phone-1 python -m eval.run_cases --case CASE_UUID
docker exec -it inbound-phone-1 python -m eval.run_cases --no-post
```

The runner reads `API_BASE_URL` and `VOICE_SERVICE_TOKEN` from its environment. Provider selection
defaults to `STT_PROVIDER=assemblyai`, `TTS_PROVIDER=speechify`, and `LLM_PROVIDER=google`;
`--stt`, `--tts`, and `--llm` can override them for a run. `--trigger` defaults to `EVAL_TRIGGER`
or `manual`. Results, scenarios, and call artifacts are written under
`voice/eval/results/cases/<UTC timestamp>/`.

For a local backend:

```sh
cd voice
API_BASE_URL=http://localhost:8000 \
VOICE_SERVICE_TOKEN=dev-only-voice-service-token \
uv run python -m eval.run_cases --list
```

Run without `--list` to execute calls and post results, or add `--no-post` to keep results local.
The optional deploy hook is disabled by default. Set `RUN_EVALS_AFTER_DEPLOY=1` in the production
environment to run evals after the deployment health check; failures are reported but do not fail
the deployment.

The eval runner sends Twilio-simulated webhooks to a local fake call-control server, switches its
fake backend route per case, and uses an isolated temporary spool. It does not create real
conversations, call the production backend for call handling, or invoke live assistant actions.
Provider requests for speech recognition, speech synthesis, and model responses remain real.

### Approximate cost

Using the rates in [`voice/config/prices.yaml`](../voice/config/prices.yaml), a roughly two-minute
case is approximately **$0.036 USD**: about $0.015 for two minutes of AssemblyAI STT, $0.010 for
1,000 characters of assistant Speechify output, $0.0085 for 20,000 Gemini 3.5 Flash Lite input
tokens plus 1,000 output tokens, and $0.002 for 200 characters of synthesized caller audio.
This is an estimate, not a measured bill; actual call duration, prompt/context size, response
length, cache hits, and provider billing granularity change the cost. The simulated call control
does not incur Twilio call charges.

The ffmpeg-free caller-audio path requests WAV from Speechify and resamples it locally. This WAV
response path has not yet been verified against the live Speechify API; validate it on the first
production run.

### Configuration

Set these values for both the API and worker:

| Variable | Default / format | Purpose |
|---|---|---|
| `PLATFORM_ADMIN_EMAILS` | Empty; comma-separated emails | Accounts allowed to open `/admin` |
| `QUALITY_JUDGE_MODEL` | `gemini-3.5-flash-lite` | Model used for conversation judgement |
| `LANGFUSE_HOST` | Empty | Optional base URL used to construct trace links |
| `GOOGLE_API_KEY` | Required for scoring | API key used by the scoring worker |

See the [deployment runbook](deployment.md) for production environment configuration.

## Admin view (web)

The Admin area is available to platform admins only. It provides a cross-workspace view of assistant quality; workspace selection does not limit the underlying admin data.

### Pages

- **Overview** shows conversations and scored counts, average score, review volume, response-time percentiles, daily quality, and a workspace comparison. The review list can be filtered by workspace and assistant, switched between needs-review-only and all conversations, and paged with Load more. Filters and the 7-, 30-, or 90-day range are stored in the URL.
- **Conversation detail** shows the score, judge model and rubric version, check results and notes, issues, objective call metrics where available, and the transcript. Rescore queues another score; Make eval case creates an evaluation case from the caller utterances. Trace links are shown only when the API provides one.
- **Evals** lists evaluation runs and cases. Expanding a run shows its failed cases and failure messages; case rows show caller turns, checks, enabled state, and source conversation where available.

### Quality contract

`needs_review` follows the backend contract: `overall <= 3 OR any check failed OR max_response_s > 3.0`. The scored checks are `answered_question`, `collected_required_details`, `language_consistent`, `no_unsupported_promises`, `ended_properly`, and `followed_instructions`. A check may be not applicable. Objective timing metrics are absent for channels without call timing.

Evaluation cases store caller utterances and transcript, assistant, and intake checks. Evaluation runs record case results and failures, plus the run trigger, commit SHA, and pass/fail summary.

The web Admin API uses generated types from `web/src/api/schema.d.ts`.
