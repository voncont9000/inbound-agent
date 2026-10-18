# Voice pipeline (M0)

Voice report results can enqueue the shared after-conversation actions documented in
[After-conversation actions](actions.md).

## Architecture

```text
Browser SmallWebRTC / Twilio Media Streams
                  │
                  ▼
           Pipecat transport
                  │
                  ▼
 AssemblyAI / Soniox / Deepgram STT
                  │
       Silero VAD + local Smart Turn v3
                  │
                  ▼
 Google Gemini API LLM (OpenRouter optional, function tools)
                  │
                  ▼
 Speechify / ElevenLabs / Deepgram TTS
                  │
                  ▼
           Pipecat transport
                  │
                  ▼
   Per-call local files via CallSink
 transcript · intake · tasks · metrics
```

`voice/bot.py` is a single entry point for the browser and Twilio transports. Pipecat's
development runner creates the requested transport and invokes the same voice pipeline.
The `CallSink` protocol isolates transcript, intake, follow-up tasks and metrics from
storage; Google Sheets and email sinks are intentionally deferred. Business behavior lives
in assistant YAML that extends a template: `AssistantConfig` contains a response language,
shared rule presets, custom policies, services, typed fields, urgency rules, live actions,
and a review-confidence threshold. Dictionary values merge recursively while assistant lists and scalars replace
template values.
`uk_law_firm.yaml` is the first template; the product model is not law-firm-specific.

## Provider choices and seams

The STT, TTS, and LLM providers are selected independently through `STT_PROVIDER`,
`TTS_PROVIDER`, and `LLM_PROVIDER`. Provider-specific Pipecat service construction is
isolated in `inbound_voice/providers.py`.

| Component | Provider | Model/configuration | Voice/key |
|---|---|---|---|
| STT | AssemblyAI | Universal-3.6 Pro Realtime; legal keyterms | `ASSEMBLYAI_API_KEY` |
| STT | Soniox | `stt-rt-v5`; English hint and legal context terms | `SONIOX_API_KEY` |
| STT | Deepgram | Nova-3, `en-GB`, legal keyterms | `DEEPGRAM_API_KEY` |
| LLM | Google Gemini API | `LLM_MODEL`, default `gemini-3.5-flash-lite` | `GOOGLE_API_KEY` |
| LLM | OpenRouter (optional) | `LLM_MODEL`, default `google/gemini-2.5-flash` | `OPENROUTER_API_KEY` |
| TTS | Speechify | Simba 3.2 | `SPEECHIFY_API_KEY`; optional `TTS_VOICE`, default `beatrice_32` (en-GB) |
| TTS | ElevenLabs | `ELEVENLABS_MODEL`, default `eleven_v4_turbo` | `ELEVENLABS_API_KEY`; required `TTS_VOICE` ID; suggested Katie X `MzqUf1HbJ8UmQ0wUsx2p`, built-in Alice alternative `Xb7hH8MSUJpSbSDYk0k2` |
| TTS | Deepgram | Aura-2 | `DEEPGRAM_API_KEY`; optional `TTS_VOICE`, default `aura-2-pandora-en` |

Pipecat's ElevenLabs WebSocket `multi-stream-input` endpoint rejects
`eleven_v4_turbo` with HTTP 400, so that model uses the HTTP streaming service instead;
`eleven_flash_v2_5` uses the WebSocket service. The HTTP service selects PCM output from
the pipeline's audio output sample rate, matching the WebSocket service.

ElevenLabs voices can be listed with `GET /v2/voices`; shared British conversational
voices can be searched with `GET /v1/shared-voices?language=en&accent=british&use_cases=conversational`.

- **Speech providers** use separate API keys, except Deepgram uses one key for either
  selected side. Google is the default LLM provider and requires `GOOGLE_API_KEY`;
  `OPENROUTER_API_KEY` is required only when `LLM_PROVIDER=openrouter`.
- **Google Gemini API** uses Pipecat's `GoogleLLMService`; the default is
  `gemini-3.5-flash-lite` because Gemini 2.5 Flash and Flash-Lite are unavailable to new
  API keys. Gemini 2.5 models explicitly disable thinking with `thinking_budget=0`; other
  Gemini models use `LLM_THINKING_LEVEL` (default `minimal`). Gemini 3.8 Flash rejects
  `minimal`, so configure a supported level such as `low` for that model.
- **OpenRouter** remains an optional OpenAI-compatible LLM provider. Its default model is
  `google/gemini-2.5-flash`; replacing the LLM service does not affect the turn or sink
  interfaces.
- **Pipecat** owns real-time orchestration, WebRTC/Twilio transports, service metrics, and
  interruption handling. Assistant templates and a generic intake tool layer keep product policy apart
  from vendor-specific adapters.

The bot queues an `LLMRunFrame` when a client connects so the system-prompt greeting is
spoken first. Client disconnects cancel the pipeline task. The `end_call` tool records its
result before sending `EndWorkerFrame` upstream, allowing the spoken goodbye to finish before
the pipeline ends.

When booking is enabled for a routed phone or tokenized web call, Calendar availability is
prefetched while route lookup completes. The booking tools are exposed only when the assistant
has booking configuration, the route reports Calendar availability, and the relevant live
action is enabled. Voice booking uses cached prefetched results when possible and records
tool round-trip timings in call metrics. For availability checks and bookings, the system starts
the tool and speaks the configured holding message concurrently; the model calls immediately
without announcing the action. This avoids the dead air or missed tool call that can occur when
the speech model is asked to announce and call in one turn. Holding messages are voice-only.

## Turn-taking and safeguards

Silero VAD detects speech activity and Pipecat's local Smart Turn v3 analyzer predicts
when the caller has finished a turn. Interruptions are enabled. The prompt speaks the
configured language, acknowledges volunteered details, asks critical fields naturally,
returns once to unanswered critical fields, and avoids repeating the same question twice
in a row. At completion it reads back collected unconfirmed critical details together and
waits for confirmation. Confirmation and end-call actions are optional and run only when
enabled; the default live-action set is empty. If enabled, `end_call` returns with
`run_llm=False` so the goodbye is spoken in the same response as the tool call. The prompt
discloses AI use, prevents legal advice and client-status disclosure,
constrains prices to the demo fee table, and applies urgency, complaint, and callback
handling from configured rules and post-call extraction. Intake models are built
dynamically from configured service field specs. Only configured live actions are exposed;
when none are enabled, the LLM context receives no tool schemas. Confirmation, manual
urgency, callback, complaint, and end-call actions remain opt-in. When live confirmation
is disabled, the assistant still reads back critical values and asks the caller to confirm
without calling a tool. Caller-final transcripts are
always checked against configured keyword urgency rules. In v1, date-deadline escalation,
callback requests, and complaints are post-call tasks; the employment unfair-dismissal
deadline rule is calculated from merged intake in code. Live tasks use source `live` and
post-call tasks use `post_call`. The live prompt offers a callback at most once per topic,
and post-call extraction does not treat contact details or an unaccepted offer as consent.

## Conversation rules and pronunciation

The shared prompt renderer adds a short `CONVERSATION RULES` section only for configured
presets. Phone prompts use “caller”; text channels use “customer”. Transfer language is
included for upset callers only when a transfer target is available and `transfer_call` is
enabled. WhatsApp disclosure is rendered separately for first and later replies; Gmail
drafts are human-reviewed and receive no disclosure rule.

Sentence urgency conditions are evaluated in the existing post-conversation extraction
request, without another model call. Matching reasons and evidence become urgent tasks.
Pronunciation entries are case-insensitive whole-term substitutions in Pipecat's provider-
agnostic TTS text filter and are not included in prompts.

The bot subclasses Pipecat's turn-analyzer stop strategy to hold finalization for
`NUMBER_HOLD_SECS` (default 2 seconds) only when the caller's trailing digits resemble an
incomplete UK phone number. Resumed speech cancels the hold and remains in the same caller
turn; complete or ordinary turns have no extra delay. Each hold is recorded in `metrics.json`
and the evaluation `caller_timeline.json`, including whether the caller resumed and the
elapsed hold time. Latency remains measured from the final caller stop after resumed speech,
or includes the full hold when the caller does not resume.

The hold strategy subclasses `TurnAnalyzerUserTurnStopStrategy` and relies on its private
fields (`_text`, `_turn_complete`, `_transcript_finalized`, `_timeout_task`,
`_wait_for_transcript`), so re-test it on Pipecat upgrades.

## Hybrid intake and provenance

`confirm_critical_fields` accepts only configured critical fields, validates each service's
dynamic Pydantic model, and writes `intake.live.json` with confirmation timestamps. After
disconnect, end-call, or pipeline completion, the Google Gemini API extractor runs for at
most 60 seconds (default `EXTRACTION_MODEL=gemini-3.8-flash`, thinking level `low`). It
receives numbered role-tagged transcript rows, live confirmations, and configured field
specs. Missing API credentials or a blank model skip extraction; errors are recorded and
do not crash the call.

To re-run extraction and merge for an existing call without starting a voice runner, use
`uv run python -m inbound_voice.extraction --run-dir runs/<call_id> [--config
config/assistants/demo_firm.yaml]`. It reads `transcript.jsonl` and `intake.live.json`,
rewrites `intake.post_call.json` and `intake.json`, and updates extraction usage/cost in
`metrics.json` when present.

`intake.post_call.json` retains extracted values, confidence, and caller evidence quotes.
Merged `intake.json` assigns each value a status (`agreed`, `live_only`, `filled`,
`filled_confirmed`, `conflict`, or `corrected`) and retains both source values, including
whether the caller affirmed a readback. Conflicts, invalid coercions, and confidence below
the assistant's review threshold are listed in `needs_review`. The extractor also creates
post-call callback and complaint tasks and checks merged dates against configured deadline
rules, deduplicating any live task for the same rule and value.
Extraction usage and estimated cost are separate from live-call LLM metrics. The merged
values and provenance are intended to map to customer context-graph facts; see
[`context-graph.md`](context-graph.md).

## Business knowledge and knowledge gaps

An optional `knowledge` config contains business FAQs and a digest path. The importer
crawls same-host websites subject to `robots.txt` and reads HTML, PDF, DOCX, TXT, and
Markdown sources. It writes a cited Markdown digest, a source manifest with token usage and
cost, and approximately 1,200-character chunks with hashes.

The digest is loaded once when assistant configuration is loaded and placed directly in
the system prompt alongside business FAQs and the configured fee list. This avoids a
latency-bearing live retrieval tool and keeps common answers available in the first LLM
response. Chunks are retained for M1 pgvector retrieval; future retrieval should run in
parallel with a turn, not as a live tool call that delays the response.

Post-call extraction includes `unanswered_questions` with the caller's exact evidence
quotes. Each question is written to `knowledge_gaps.json` and creates a `knowledge_gap`
task with source `post_call`. Re-extraction writes the same artifact and tasks.

```sh
uv run python -m inbound_voice.knowledge import \
  --assistant config/assistants/demo_firm.yaml \
  --url https://example.com \
  --file docs/faq.md
```

`KNOWLEDGE_DIGEST_PATH` overrides the YAML digest path. The named `knowledge_check` matrix
scenario is not part of the default scenarios; use `--knowledge-fixture` to import the
local Demo & Co site and client-care document before running it.

The goodbye detector remembers caller closing intent for the whole call. If the caller
previously said goodbye and the assistant later says goodbye (including after a readback
confirmation), it queues `EndWorkerFrame` 1.5 seconds after `BotStoppedSpeaking` unless
the caller starts speaking again. Without caller closing intent, an assistant goodbye at
the end of its final sentence also triggers the hang-up; mentioning goodbye before a
question does not. The task source is `goodbye_detected`.

Separately, after bot speech and `IDLE_PROMPT_SECS` (default 8) of caller silence, an idle
prompt is queued. After a second idle interval it says “I'll end the call now. Thank you
for calling. Goodbye.” and ends the call after speech stops, with an `end_call` task source
of `idle`. Caller speech resets the timer, and neither idle message is queued while the bot
is speaking.

`SMART_TURN_STOP_SECS` controls Pipecat's silence fallback when Smart Turn marks an
utterance incomplete; it defaults to `0.7` seconds.
The matrix runner accepts `--smart-turn-stop-secs`, sets it for each runner, and records the
value in JSON results and summary rows. Its summary reports caller-stop→turn-end,
turn-end→LLM-first-token, LLM-first-token→TTS-first-audio medians, and LLM TTFB.

Employment and conveyancing scripted callers append a final confirmation (“Yes, that's all
correct. Thanks, bye.”), then wait up to 25 seconds for the server to disconnect. The
caller timeline records whether the disconnect was detected and seconds since the end of
the agent's last speech; the score summary surfaces both. If the server disconnects before
all scripted lines are played, the caller stops cleanly without recording a caller error.
The named `idle_check` scenario sends one property-purchase utterance and remains silent;
it scores the `idle` end-call source and server disconnect. Its final wait is 35 seconds
to allow the initial response, both 8-second idle intervals, and goodbye speech;
employment and conveyancing wait up to 25 seconds. It is run only when explicitly
selected, for example:

```sh
uv run python -m eval.run_matrix \
  --stt soniox --tts speechify --llm google --scenario idle_check --reps 1
```

At the 2.0-second setting, baseline stage medians were:

| Scenario | Caller stop→turn-end | Turn-end→LLM first token | LLM first token→TTS first audio |
|---|---:|---:|---:|
| Employment (8 turns) | 2.200 s | 0.276 s | 0.399 s |
| Conveyancing (7 turns) | 0.305 s | 0.170 s | 0.309 s |

These are one baseline call per scenario, not a p95 guarantee. Lower endpointing settings
must be evaluated for interruption/cut-off risk as well as latency.

Turn-stop matrix comparison (aggregate checks, median of per-call p50, maximum per-call
p95). The 2.0s, 1.0s, and 0.5s rows are from earlier two-call-per-scenario runs; 0.7s
includes three repetitions each for employment and conveyancing. Its pooled p95 is
calculated over every measured turn across all six calls:

| Smart turn stop | Checks | Aggregate p50 | Maximum per-call p95 | Pooled all-turn p95 |
|---:|---:|---:|---:|---:|
| 2.0s | 41/50 | 1.684s | 3.608s | — |
| 1.0s | 43/50 | 1.204s | 2.992s | — |
| 0.5s | 42/50 | 1.481s | 2.425s | — |
| 0.7s | 78/87 | 1.221s | 2.088s | 1.946s |

## Latency and cost

The latency target is **p95 ≤ 1.5 seconds** from when the caller stopped speaking (VAD stop time
minus its `stop_secs` confirmation silence) to the first `BotStartedSpeakingFrame` from the transport output. M0 also records the separate
Smart-Turn-end-to-first-bot-speaking interval. It keeps only the latest pending caller
turn, clears it on interruption, and reports p50, p95, and max for both intervals. Other
measurements include Pipecat STT/LLM/TTS TTFB-family metrics, token usage, synthesized
characters, and call duration.

Estimated cost uses provider-specific STT per-minute rates multiplied by full call
duration (the stream/session remains open for the call), provider-specific TTS rates per
1,000 synthesized characters, model-specific Google Gemini or OpenRouter input/output rates
per million tokens, a separate post-call extraction component, and UK local Twilio inbound
per minute. Measured STT audio seconds remain in usage
metrics for information but do not determine STT cost. `prices.yaml` records vendor
sources and check dates; Google Gemini prices use the official
[Gemini API pricing page](https://ai.google.dev/gemini-api/docs/pricing), paid-tier text
rates. Use the paid tier for client data because Google's free tier may use prompts and
responses to improve its products. OpenRouter prices are retained for the optional provider.
Older metrics without provider metadata default to Deepgram and Gemini 2.5 Flash.
Missing price or usage components are reported as unknown, not silently treated as a
zero-cost service. Twilio number rental and taxes are excluded.

The backend stores the call report's `turn_latency_summary` in `conversations.metrics` and the
cost estimate in `conversations.estimated_cost`. The Home dashboard shows the median of the calls'
p50 response latency and splits cost into telephony (`twilio`) and AI (everything else); see
[m1-architecture.md](m1-architecture.md#home-dashboard).

## Provider research (2026-10-17)

The following are **public benchmarks and vendor list prices, not measurements from this
project**:

| Component | Option | Quality | Latency | Price | ~Per 3-min call |
|---|---|---|---|---|---|
| STT | AssemblyAI Universal-3.6 Pro | 2.2% WER (Coval) | 133 ms time-to-final (Coval) | $0.45/hr | $0.0225 |
| STT | Soniox stt-rt-v5 | 4.8% WER (Coval) | 45 ms time-to-final (Coval) | $0.12/hr | $0.006 |
| STT | Deepgram Nova-3 | 5.3% WER (Coval) | 85 ms time-to-final (Coval) | $0.29/hr | $0.0144 |
| TTS | Speechify Simba 3.2 | Vapi Humanness 97 (joint #1); Artificial Analysis #9 | 125 ms TTFA (Coval) | $10/1M chars | $0.015 |
| TTS | ElevenLabs v4 Turbo | Artificial Analysis #1 (Elo 1330) | 198 ms TTFA (Coval) | $40/1M chars | $0.06 |
| TTS | Deepgram Aura-2 | Vapi Humanness ~57 | 297 ms TTFA (Coval) | $30/1M chars | $0.045 |
| LLM | Gemini 2.5 Flash (OpenRouter) | — | ~0.6 s TTFT median (LiveKit) | $0.30/$2.50 per 1M tokens | $0.017 |
| LLM | Gemini 2.5 Flash Lite (OpenRouter) | test tool-calling first | ~0.7 s p50 via OpenRouter | $0.10/$0.40 per 1M tokens | $0.005 |

Assumptions: 3-minute call, ~1,500 agent characters, ~45k input / 1.5k output tokens,
Twilio UK inbound ~$0.03 extra. Recommended default: AssemblyAI + Gemini 2.5 Flash +
Speechify ≈ $0.09/call incl. Twilio; Soniox is the cheaper STT fallback; ElevenLabs is
the premium voice (~$0.135/call with AssemblyAI). Sources: humannessindex.vapi.ai,
artificialanalysis.ai/text-to-speech/leaderboard/provider-voice, benchmarks.coval.ai,
livekit.com/benchmarks/latency.

## Scripted A/B calls

### First scripted smoke call (2026-10-17)

AssemblyAI + Speechify, employment scenario: headline VAD-stop-to-audio p50 **3.40 s** /
p95 **5.12 s**; turn-end-to-audio p50 **1.15 s**; **4/12** checks; estimated cost
**$0.033**. The agent did not call `end_call` and never called `save_intake`, leaving
`intake.json` empty and preventing the deadline urgency check from running.

### Post-fix scripted smoke call (2026-10-17)

After adding the generated intake schema, explicit tool-use instructions, and testing with
`SMART_TURN_STOP_SECS=2.0`, the same AssemblyAI + Speechify employment scenario
reported headline VAD-stop-to-audio p50 **2.45 s** / p95 **3.99 s**; turn-end-to-audio
p50 **1.21 s**; **4/12** checks; estimated cost **$0.029**. `end_call` fired, but
`save_intake` still did not: `intake.json` had no practice area or fields, and no urgent
task was recorded. Four of eight turns had a ~2.2-second VAD-stop-to-turn-end gap,
down from ~3.2 seconds in the first run.

### Third scripted smoke call (2026-10-17)

The next AssemblyAI + Speechify employment run scored **8/12**. Tool-call logging showed
six `save_intake` calls, all successful, and the call recorded `urgent`, `callback`, and
`end_call` tasks. Headline VAD-stop-to-audio latency was p50 **3.05 s** / p95 **5.03 s**;
turn-end-to-audio p50 was **1.87 s**, up from about **1.15 s** because tool-call turns
need a second LLM round trip. The caller's name was still misheard as “Mac Alistair,” and
the email was captured as `yvonne.mcallister`.

### Settled 16-call provider matrix (2026-10-17)

The two-repetition employment calls used OpenRouter Gemini 2.5 Flash. Soniox scored
**10/12** in all four calls; AssemblyAI scored **8–9/12**. AssemblyAI misheard the email
(`yvonne.mcallister@…`) in all four calls and the phone's extra zero (“9000”) in three of
four. Soniox captured both correctly in all four calls. Both providers struggled with the
surname “McAllister”. Turn-end-to-audio p50 ranged from **2.9–3.6 s**.

Estimated cost per roughly 2.4-minute call was about **$0.022** for Soniox + Speechify,
**$0.044** for Soniox + ElevenLabs, **$0.037** for AssemblyAI + Speechify, and **$0.055**
for AssemblyAI + ElevenLabs. All eight conveyancing calls timed out after the first caller
utterance because the caller harness used a fixed 25-second deadline while the agent was
still speaking; the caller-turn fix now waits for speech to start and then allows up to
90 seconds for the agent turn to finish. These are synthetic callers, not a substitute for
real UK callers.

### Soniox + ElevenLabs Flash v2.5 conveyancing smoke (2026-10-17)

The Soniox + ElevenLabs Flash v2.5 conveyancing run scored **11/13**. Headline latency
was p50 **2.20 s** / p95 **2.74 s**, with turn-end-to-audio p50 **1.90 s** and TTS
TTFB **0.165 s**. Estimated cost was about **$0.058**; this uses the single ElevenLabs
list rate in `voice/config/prices.yaml`. Flash v2.5-specific pricing has not been
verified and may differ.

The harness plays scripted utterances in a fixed order regardless of the agent's question,
so a caller's next answer can be misaligned with what the agent asked.

## Deferred

M0 writes local files only. Google Sheets and email sinks, the customer context graph,
multi-tenancy, persistence/retention controls, practice-management connectors, and
production operations are later work. Demo fees are fictional and must be replaced with
firm-approved prices before any real client-facing use.

## Business instructions, readback, and caller ID

Assistant behavior is configured in each business's multiline `instructions` field and
formatted with the business name, greeting, and office-hours placeholders. The prompt
renderer contributes identity, business data, knowledge, and feature mechanics; it does not
hard-code callback or other call-behavior policy. The channel-neutral business context,
intake extraction, and pricing helpers live in `core/inbound_core` and are shared with text
channels; voice-specific readback, caller ID, and tool mechanics remain in the voice service.
`readback` selects `off`, `email`, or `critical` behavior. The law-firm template selects
`email`.
Email read-backs spell addresses as separate letters and spoken words such as “dot” and
“at,” rather than punctuation symbols; common domains are spoken as words.

Twilio caller identity is taken from the start message's `From` custom parameter. For local
and evaluation WebRTC calls only, `CALLER_ID_OVERRIDE` supplies a test caller number. The
number is added to the prompt and post-call phone intake with `source: caller_id` and
confidence `1.0`; a different number stated by the caller wins and keeps caller ID in
`alternatives`.

The incomplete-phone-number stop strategy gates turn finalization, so an active hold cannot
fall through to Pipecat's parent stop strategy. A likely partial UK number holds for at most
`NUMBER_HOLD_SECS` (default `2.0`); caller resumption continues the turn, and timeout releases
it. The decision-time transcript text and each hold's start, duration, and resumption are
recorded for evaluation. Paused scripted utterances stay one caller turn while the caller
timeline records their separate speech segments, allowing checks for agent speech during a
pause.

Scripted A/B calls use synthetic TTS caller audio band-limited to 8 kHz; this is not a
substitute for real UK callers.

## API mode

Set both `API_BASE_URL` and `VOICE_SERVICE_TOKEN` to enable tenant-aware API routing and
call reporting. The voice service sends `X-Service-Token` on the internal route, start, and
report requests. `ROUTE_TIMEOUT_SECONDS` defaults to 2 seconds. WebRTC calls also require
`WEB_CALL_TO_NUMBER`; without it the runner uses the local `ASSISTANT_CONFIG`. Phone calls
use the dialled `To` number supplied by Twilio.

The route lookup runs before the greeting. If routing fails or times out, the call uses the
local assistant YAML, while retaining the API call metadata so the start and report are
still attempted. The call-start request is started asynchronously after pipeline setup.
After intake finalization and metrics persistence, the voice service posts the report.
Start and report requests retry up to five times on transport errors, timeouts, HTTP 408,
429, and 5xx responses, with 0.5, 1, 2, and 4 second backoffs. Route lookup is a single
attempt; it does not add retry latency before the greeting.

If the report cannot be delivered, one atomic JSON record is written to `VOICE_SPOOL_DIR`
(default `voice/spool/`). Safe external call IDs become filenames directly; other IDs use
`call.<first-16-hex-of-sha256>.json` so untrusted values cannot escape the directory or
collide with safe IDs. The spool stores the call start, report, conversation ID when
available, last error, and spool timestamp; it never stores the service token. Retry
delivery with:

```sh
uv run python -m inbound_voice.api_client resend [--spool-dir DIR]
```

The command reports sent and remaining records and exits nonzero while any remain. Local
`runs/<call_id>/` artifacts are written in both API and local modes.

## Twilio phone transport

Run the standalone server from `voice/` and point the Twilio Voice webhook at
`https://<public-host>/twilio/voice`:

```sh
uv run phone_server.py --port 7861
```

The server binds to `127.0.0.1` by default; a local tunnel or reverse proxy can forward
the public webhook and media stream to that loopback listener. Use `--host 0.0.0.0` only
when direct network access is intentional and protected by the surrounding network.

The server exposes `/healthz`, a form webhook at `/twilio/voice`, and a Media Streams
WebSocket at `/twilio/ws`. Set `TWILIO_AUTH_TOKEN` to validate `X-Twilio-Signature`; set
`PUBLIC_BASE_URL` when running behind a proxy or tunnel so signature validation and the
generated `wss://` stream URL use the public origin. Without `PUBLIC_BASE_URL`, the server
honors `X-Forwarded-Proto` and `X-Forwarded-Host`. Signature validation is skipped with a
warning when no auth token is configured; production deployments must configure it.
With the token configured, the webhook adds `StreamToken` and `StreamIssuedAt` custom
parameters. The HMAC-SHA256 token binds the Twilio Call SID, `To`/`From` numbers, and
issue time. Streams expire after 120 seconds, allow at most 30 seconds of clock skew into
the future. Accepted Call SIDs are claimed for a 150-second replay window. Set `REDIS_URL`
to share these claims across phone-server processes; without it, replay protection is
in-memory per process. If Redis is unavailable, the server logs an error and falls back to
its local check so a Redis outage does not drop calls. The media-stream WebSocket checks
these before preparing a call and closes unauthorized or replayed streams with code 1008.
Without the token, webhook signature and stream-token validation are skipped for local
development.
`TWILIO_ACCOUNT_SID` plus `TWILIO_AUTH_TOKEN` enable REST hang-up. Without both, the
WebSocket is closed when the pipeline ends.

The phone pipeline uses 8 kHz mono PCM internally and the Pipecat Twilio serializer
converts to/from Twilio's 8 kHz G.711 μ-law frames. Pipecat 1.12.0 source inspection found
the selected provider services can be configured for these 8 kHz paths:

| Component | Phone rate | Pipecat 1.12.0 finding |
|---|---:|---|
| Twilio Media Streams | 8 kHz μ-law | Serializer decodes/encodes at 8 kHz |
| AssemblyAI STT | 8 kHz input | `sample_rate` is passed to its realtime connection |
| Soniox STT | 8 kHz input | `sample_rate` is passed in the service audio settings |
| Deepgram STT | 8 kHz input | `sample_rate` is passed to its realtime service |
| Silero VAD | 8 kHz input | Supports 8 and 16 kHz |
| Local Smart Turn v3 | 8 kHz input | Resamples internally to 16 kHz |
| Speechify HTTP TTS | 8 kHz output | Supports PCM output at the requested rate |
| ElevenLabs HTTP and WebSocket TTS | 8 kHz output | Maps 8 kHz to `pcm_8000` |
| Deepgram TTS | 8 kHz output | Sends the configured rate in the request query |

No provider-specific rate override is needed for phone audio. The browser pipeline keeps
its existing 16 kHz input and 24 kHz output rates. These findings verify Pipecat's
sample-rate configuration paths; provider account/model acceptance was not exercised
against live services.

Hosted browser practice calls can use same-origin WebSocket PCM at 16 kHz input and 24 kHz
output for HTTPS reverse proxies that cannot forward WebRTC's direct UDP media.
An owner-generated assistant test link carries a signed, short-lived `call_token`; the
browser server resolves its assistant configuration and knowledge digest through the
internal voice API and fails closed when that route cannot be verified.

## Local Twilio simulator

`eval.twilio_sim` signs and posts the webhook, parses the returned TwiML, opens a local
WebSocket, and sends paced 20 ms μ-law frames. It writes `caller_audio.wav`,
`bot_audio.wav`, and a caller timeline compatible with the existing evaluation scorer.
Caller utterances are resampled from the cached 48 kHz synthetic audio. To run the fake
API as a standalone process for development:

```sh
uv run python -m eval.fake_api --port 8010 --token dev-token --to +441234567890
```

The fake API also binds to `127.0.0.1` by default; pass `--host` to choose another
interface. Its `/_fake/state` inspection endpoint requires the same `X-Service-Token`
header as the voice routes.

The matrix runner can start its own fake API and the phone server:

```sh
uv run python -m eval.run_matrix \
  --stt soniox --tts speechify --llm google \
  --scenario employment --reps 1 --transport twilio --fake-api
```

Results include the transport and whether the matching external call report was delivered.
The same runner also supports `--transport webrtc --fake-api`. A standalone call can be
driven with `uv run python -m eval.twilio_sim --scenario employment --output-dir DIR`.

Post-call extraction includes a neutral one- or two-sentence call summary and resolves a
clear callback preference to `preferred_time_iso` using the configured office-hours
timezone (Europe/London by default). Callback task `due_at` values must be aware and within
14 days of call end; an out-of-hours time moves to the next opening, and missing or invalid
preferences use the next opening when office hours are configured.
## Live call transfer

Assistants can configure `transfer_targets` (key, label, phone, `when_to_use`, and
`office_hours_only`) and enable the `transfer_call` live action. Phone numbers are
normalized to E.164. Office-hours-only targets use the assistant timezone and the
half-open interval from `opens` through (but not including) `closes`. The tool and prompt
section appear only when the action is enabled and at least one target is available; text
channels do not expose it.

On a successful tool call, the system speaks `transfer_handoff_message` (default:
“I'm putting you through now, please hold.”); the model is instructed to call
`transfer_call` without announcing the transfer. Acceptance is recorded before the result
callback. A call-scoped task then prepares the transfer, pushes the handoff line and marker,
waits for playback, starts the redirect, and sends `EndWorkerFrame`. Pipecat may cancel the
function handler on caller interruption, but cannot cancel this accepted-transfer sequence.
The caller is muted by the user aggregator from the next input frame after acceptance and remains
muted through pipeline end; this suppresses input audio, VAD/user-speaking frames, transcriptions,
and interruption frames, preventing another user turn from reaching the LLM.

The marker reaches the tracker from the output transport only after earlier queued audio has been
written, so model speech already in flight and the handoff line remain in output order. Its status
is `played`, `interrupted`, or `timeout`: an output-transport interruption resolves pending markers
as interrupted, and a later marker cannot change that result. Transfer waits at most 10 seconds;
it allows a 0.2-second grace period only when the line played. Interrupted and timed-out lines
start the redirect immediately. The pipeline-end `complete_transfer` path remains a fallback.

The handler starts the Twilio call redirect before sending `EndWorkerFrame`. This ordering
is required because Pipecat's FastAPI websocket transport
closes the websocket when the pipeline ends on `EndFrame`; closing the `<Connect><Stream>`
before the REST redirect would make Twilio hang up the call. The call-finalization path
reuses the start result and waits for its outcome callback.

Phone calls use Twilio Dial TwiML, an answered callback for prompt outcome reporting, and
the Dial action callback for the final result. Callback URLs use the configured public
base URL or forwarded request host and are signature-validated when `TWILIO_AUTH_TOKEN`
is configured. The configurable `transfer_failed_message` is spoken when a dial fails.
Reports include `metrics.transfer` and end reason `transferred`; no-answer, busy, failed,
canceled, and start errors create a `transfer_failed` task. Browser/practice calls
simulate transfer outcomes.

`TWILIO_CALL_CONTROL=rest` selects Twilio REST control; it is selected by default when
both Twilio credentials are set. Otherwise call control defaults to the fake. Set
`TWILIO_CALL_CONTROL=fake` and optionally `TWILIO_FAKE_CALL_LOG` for local evaluation.
`TRANSFER_OUTCOME_WAIT_SECS` controls the callback wait (default 35 seconds). The
callback registry is process-local: a callback reaching another process gets valid
failure TwiML, but the original process reports the outcome as `unknown`.

The `transfer_request` evaluation scenario uses fake call control and a signed Dial
callback; the real provider scenario is not required for unit-test plumbing.

## Production voice services

The production Compose stack runs the phone and browser-call servers as separate containers behind
Caddy. Set `TWILIO_MODE=off` until a production Twilio auth token and webhook are configured;
`twilio` mode requires `TWILIO_AUTH_TOKEN`. In production the browser-call WebSocket requires a
backend `call_token`, or a valid configured `WEB_CALL_TOKEN` for demo access. The proxy routes
`/twilio/*` to the phone service and `/call/*` to the browser-call service; the two services are
not published directly on the host.
