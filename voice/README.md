# Inbound voice prototype (M0)

This prototype runs a configurable inbound voice assistant with browser WebRTC as the
primary test path and a Twilio Media Streams transport for later phone-number testing.
Businesses use assistant configuration files; UK law firms are the first template.

## Setup

Install [uv](https://docs.astral.sh/uv/) and Python 3.12. From this directory:

```sh
uv sync
cp .env.example .env
```

Set `GOOGLE_API_KEY` and the API key for each selected speech provider in `.env`.
`OPENROUTER_API_KEY` is needed only when `LLM_PROVIDER=openrouter`. The default selection is
`STT_PROVIDER=assemblyai` and `TTS_PROVIDER=speechify`, requiring
`ASSEMBLYAI_API_KEY` and `SPEECHIFY_API_KEY`. Use `SONIOX_API_KEY` for Soniox,
`ELEVENLABS_API_KEY` for ElevenLabs, and `DEEPGRAM_API_KEY` when either provider is
Deepgram. The demo business, fees, and price table are illustrative.

## Assistant configuration

`ASSISTANT_CONFIG` selects a business configuration (default
`config/assistants/demo_firm.yaml`). It extends a template in `config/templates/`; the first
template, `uk_law_firm.yaml`, supplies conveyancing and employment services, field
definitions, safeguards, urgency rules, and configurable live actions. Its live-action
default is empty. Assistant files override
template values using recursive dictionary merging; lists and scalar values replace the
template values. Service fields and actions are data-driven so a future settings UI can
edit the same configuration shape.

Critical fields are read back and confirmed verbally; the optional
`confirm_critical_fields` action records that confirmation live when enabled. Other intake
details are extracted after the call. `intake.live.json` records caller-confirmed values,
`intake.post_call.json` records extracted values, and merged `intake.json` preserves
provenance, confidence, alternatives, and `needs_review` flags for conflicts, invalid
values, or confidence below the configured threshold.

The assistant speaks the configured `language` (the UK law-firm template uses British
English). The prompt acknowledges volunteered details, asks critical questions naturally,
returns once to unanswered critical fields, and avoids repeating the same question twice
in a row. Before ending, it reads back collected unconfirmed critical details together and
waits for confirmation. Enabled actions are called only when configured.

Re-run extraction and the live/post-call merge for an existing call without starting a
runner:

```bash
uv run python -m inbound_voice.extraction --run-dir runs/<call_id> [--config config/assistants/demo_firm.yaml]
```

The command reads `transcript.jsonl` and `intake.live.json`, rewrites
`intake.post_call.json` and `intake.json`, and recalculates the extraction usage/cost in
`metrics.json` when that file exists.

## Business knowledge

Assistant YAML may define optional FAQs and a digest path:

```yaml
knowledge:
  sources: [{url: https://example.com}, {file: docs/faq.md}]
  faqs:
    - question: "Do you offer video meetings?"
      answer: "Yes, via Microsoft Teams."
  digest_path: knowledge/demo_firm/digest.md
  max_digest_chars: 24000
```

Import websites and supported documents with:

```sh
uv run python -m inbound_voice.knowledge import \
  --assistant config/assistants/demo_firm.yaml \
  --url https://example.com \
  --file docs/faq.md \
  --max-pages 50 \
  --out knowledge/demo_firm
```

The importer writes `digest.md`, `chunks.jsonl`, and `manifest.json`. The digest is loaded
once with the assistant configuration and added to the system prompt; `KNOWLEDGE_DIGEST_PATH`
overrides the configured `digest_path` and may be set in `.env`. Knowledge sources in YAML
are descriptive; import inputs come from the CLI.

## Selecting providers and voices

Change `STT_PROVIDER` and `TTS_PROVIDER` in `.env`, then restart the runner to A/B providers.
Supported STT values are `assemblyai`, `soniox`, and `deepgram`; supported TTS values are
`speechify`, `elevenlabs`, and `deepgram`. `LLM_PROVIDER` defaults to `google`; the optional
`openrouter` provider remains available. `LLM_MODEL` defaults to `gemini-3.5-flash-lite` for Google
and `google/gemini-2.5-flash` for OpenRouter.

Google Gemini 2.5 models explicitly disable thinking with `thinking_budget=0`; other Gemini
models use `LLM_THINKING_LEVEL` (default `minimal`). Gemini 2.5 Flash and Flash-Lite are
unavailable to new API keys. Gemini 3.8 Flash rejects `minimal`; use a supported level such
as `low`. Google's free tier may use prompts and responses to improve its products, so use
the paid tier for client data. Paid-tier text prices per 1M input/output tokens are
$0.30/$2.50 for 3.5 Flash-Lite, $0.25/$1.50 for 3.1 Flash-Lite, and $0.75/$3.75 for 3.8
Flash (scheduled to increase to $1.50/$7.50 on 2027-01-01), from the official
[Gemini API pricing page](https://ai.google.dev/gemini-api/docs/pricing).

Post-call extraction defaults to `EXTRACTION_MODEL=gemini-3.8-flash` with low thinking and
uses the Google Gemini API directly. It is skipped when `GOOGLE_API_KEY` is absent or the
model setting is blank. Extraction errors are recorded without ending the call, and
extraction tokens are priced separately from live LLM usage.

`TTS_VOICE` is optional for Speechify and Deepgram. Speechify defaults to `beatrice_32`
(a user-verified en-GB voice for Simba 3.2). List available Speechify voices with:

```sh
curl -G https://api.speechify.ai/v1/voices \
  -H "Authorization: Bearer $SPEECHIFY_API_KEY" \
  -d locale=en \
  -d model=simba-3.2
```

For ElevenLabs, set `TTS_VOICE` to a voice ID. The suggested shared-library voice is
Katie X — Clear British Customer Support (`MzqUf1HbJ8UmQ0wUsx2p`); built-in Alice
(`Xb7hH8MSUJpSbSDYk0k2`), a British RP voice, is an alternative. Find personal voices
with `GET /v2/voices` or search shared British conversational voices with
`GET /v1/shared-voices?language=en&accent=british&use_cases=conversational`:

```sh
curl -H "xi-api-key: $ELEVENLABS_API_KEY" https://api.elevenlabs.io/v2/voices
curl -G https://api.elevenlabs.io/v1/shared-voices \
  -H "xi-api-key: $ELEVENLABS_API_KEY" \
  -d language=en \
  -d accent=british \
  -d use_cases=conversational
```

```dotenv
TTS_PROVIDER=elevenlabs
TTS_VOICE=MzqUf1HbJ8UmQ0wUsx2p
ELEVENLABS_MODEL=eleven_v4_turbo
```

The default ElevenLabs model is `eleven_v4_turbo`. Pipecat's WebSocket
`multi-stream-input` endpoint rejects this model with HTTP 400, so the bot uses Pipecat's
HTTP streaming service for it. `eleven_flash_v2_5` uses the WebSocket service. The HTTP
service selects PCM output using the pipeline's audio output sample rate, matching the
WebSocket service.

## Turn-taking

`SMART_TURN_STOP_SECS` defaults to `0.7` seconds. Pipecat uses this silence timeout when
Smart Turn judges an utterance incomplete. `NUMBER_HOLD_SECS` defaults to `2.0` seconds and
holds only turns ending in a likely partial UK phone number; resumed speech stays in the same
turn, while ordinary turns have no added delay. Set `IDLE_PROMPT_SECS` (default `8`) to control
the idle interval: after bot speech, the assistant asks “Are you still there?” and, after a
second idle interval, says “I'll end the call now. Thank you for calling. Goodbye.” before
ending the call. Caller speech resets the timer. The live prompt limits callback offers to
once per topic and continues with the caller's questions if they do not clearly accept.

Scenario utterances may use `| pause 1.4 |` to split caller speech into separately synthesized
segments with silence between them; the segments still count as one caller line.

The evaluation runner accepts `--smart-turn-stop-secs` to set and record the value for
each runner. Matrix summaries include caller-stop→turn-end, turn-end→LLM-first-token,
LLM-first-token→TTS-first-audio stage medians and LLM TTFB. The template has no live
actions enabled by default; each action, including `end_call`, can be enabled per
assistant. When `end_call` is enabled, the goodbye is spoken in the same LLM response as
the tool call, avoiding a follow-up inference. Caller-final keyword urgency rules always
run live; date-deadline checks, callback requests, and complaints become post-call tasks.
The goodbye detector remembers caller closing intent for the whole call. If the caller
previously said goodbye and the assistant later says goodbye (including after a readback
confirmation), the bot hangs up 1.5 seconds after bot speech stops unless the caller starts
speaking again. Without caller closing intent, a goodbye at the end of the assistant's
final sentence also triggers the hang-up; mentioning goodbye while asking a question does
not. The scripted callers confirm the final readback and wait up to 25 seconds for server
disconnect. Their timeline and score summary record the disconnect and the time since the
agent's last speech. A disconnect before all caller lines finish is treated as an early
clean stop.

The named `idle_check` scenario has a single property-purchase utterance followed by
silence. It checks for the idle `end_call` source and server disconnect after the
“Are you still there?” prompt and second idle interval. Its final wait is 35 seconds to
allow the agent's initial response, both 8-second idle intervals, and goodbye speech;
employment and conveyancing keep a 25-second final wait. It is not selected by default; run
it explicitly:

```sh
uv run python -m eval.run_matrix \
  --stt soniox --tts speechify --llm google --scenario idle_check --reps 1
```

The 2.0s, 1.0s, and 0.5s rows summarize the previous two-call-per-scenario matrices. The
0.7s row summarizes three repetitions each for employment and conveyancing. Its pooled
p95 is calculated over every measured turn in all six calls.

| Smart turn stop | Checks | Aggregate p50 | Maximum per-call p95 | Pooled all-turn p95 |
|---:|---:|---:|---:|---:|
| 2.0s | 41/50 | 1.684s | 3.608s | — |
| 1.0s | 43/50 | 1.204s | 2.992s | — |
| 0.5s | 42/50 | 1.481s | 2.425s | — |
| 0.7s | 78/87 | 1.221s | 2.088s | 1.946s |

## Browser web call

```sh
uv run --env-file .env bot.py
```

Open the local runner's URL (normally `http://localhost:7860`) and select **WebRTC** to
start a browser call. The agent greets the caller when the client connects. When it
finishes a conversation with `end_call`, it speaks its goodbye before ending the pipeline.
The page is served by Pipecat's development runner. Real speech processing requires the
API keys.

## Hosted browser call

The hosted browser-call server sends 16 kHz PCM microphone audio and receives 24 kHz PCM
playback over a same-origin WebSocket. This works through HTTPS reverse proxies that do
not forward the direct UDP media required by WebRTC. It is a development and practice tool;
it binds to `127.0.0.1` by default and should remain behind a trusted local preview proxy.
Use headphones so the agent does not hear its own audio.

```sh
STT_PROVIDER=soniox uv run --env-file .env python web_call_server.py \
  --host 127.0.0.1 --port 7862
```

Open the page at `/`. Set `WEB_CALL_TO_NUMBER` to route through the configured voice API,
`WEB_CALL_TOKEN` to require a shared token (pass it as `?token=...`), and
`WEB_CALL_MAX_CONCURRENT` to change the in-process call limit (default `2`). Provider keys
are read from the process environment or `.env`.

For owner-created assistant test calls, configure `API_BASE_URL` and the matching
`VOICE_SERVICE_TOKEN` in the voice service, and set backend `WEB_CALL_BASE_URL` to the
browser-call page URL (for example, `http://127.0.0.1:7862/`). The API issues a signed
10-minute `call_token`; tokenized calls route to that assistant and fail closed if the API
route is unavailable. Calls without a `call_token` retain the local-config behavior.

## Twilio phone server

Configure `TWILIO_AUTH_TOKEN` to validate incoming webhook signatures. Add
`TWILIO_ACCOUNT_SID` when REST hang-up is desired; otherwise the server closes the
WebSocket after the shared pipeline ends. The phone server binds to `127.0.0.1` by
default, which works with a local tunnel such as ngrok. Set `PUBLIC_BASE_URL` to the
tunnel's HTTPS URL:

```sh
ngrok http 7861
PUBLIC_BASE_URL=https://YOUR_NGROK_HOST uv run --env-file .env phone_server.py --port 7861
```

Configure the Twilio number's incoming Voice webhook to
`https://YOUR_NGROK_HOST/twilio/voice` using HTTP POST. The service returns TwiML that
connects the stream to `/twilio/ws`. `/healthz` is available for readiness checks. The
Pipecat development runner's `-t twilio` path also remains available for development.

The phone pipeline uses 8 kHz PCM internally and Twilio's 8 kHz μ-law stream externally.
Set `PUBLIC_BASE_URL` behind a proxy so signature validation uses Twilio's public request
URL. With `TWILIO_AUTH_TOKEN` configured, the webhook adds a `StreamToken` HMAC-SHA256
parameter bound to CallSid, To, and From; the WebSocket verifies it before call setup and
closes unauthorized streams with code 1008. If directly binding to a network interface is
required, pass `--host` explicitly (for example, `--host 0.0.0.0`) and protect access with
the surrounding network controls. Never disable signature validation in a public
deployment.
Set `REDIS_URL` to a shared Redis instance (the Compose service is available at
`redis://localhost:6379/0`) to share the 150-second CallSid replay window across
phone-server processes. Without it, claims are in-memory per process. If Redis is
unavailable, replay protection logs the error and falls back to a local check so a Redis
outage does not drop real calls.

## API routing and reporting

Set `API_BASE_URL` and `VOICE_SERVICE_TOKEN` to use tenant-aware routing and call reports.
WebRTC API routing also requires `WEB_CALL_TO_NUMBER`; otherwise it uses local
`ASSISTANT_CONFIG`. The phone webhook supplies its `To` and `From` numbers. Route lookup
occurs before the greeting with a default 2-second timeout; a failed route uses local YAML
but still attempts reporting. Start and report calls retry transient failures. Undelivered
reports are atomically written to `VOICE_SPOOL_DIR` (default `spool/`) and can be resent:

```sh
uv run python -m inbound_voice.api_client resend
```

`VOICE_SPOOL_DIR`, `API_BASE_URL`, service tokens, and `.env` values are local configuration;
never commit `.env`, call runs, or spool files.

## Local Twilio simulation

Run the phone server and, in a second terminal, the μ-law simulator:

```sh
uv run phone_server.py --port 7861
uv run python -m eval.twilio_sim --scenario employment --output-dir runs/twilio-smoke
```

For routing and report-delivery checks without a backend, use the matrix runner's in-process
fake API:

```sh
uv run python -m eval.run_matrix --stt soniox --tts speechify \
  --scenario employment --reps 1 --transport twilio --fake-api
```

The standalone fake API also binds to `127.0.0.1` by default; its `/_fake/state` endpoint
requires the configured `X-Service-Token`.

## Call outputs and report

Each session writes `transcript.jsonl`, `intake.live.json`, `intake.post_call.json`,
`intake.json`, `tasks.json`, `tool_calls.jsonl`, and `metrics.json` to
`voice/runs/<call_id>/`. That directory is ignored by Git. Call-level
metrics include
VAD-stop-to-first-bot-speaking and Smart-Turn-end-to-first-bot-speaking latency for each
turn, selected STT/TTS providers and LLM model, Pipecat service latency and usage
measurements, extraction usage, and estimated cost. STT cost is based on the full call duration because the
stream/session stays open for the call; measured STT audio seconds are retained as
informational usage data. TTS cost uses synthesized characters and LLM cost uses the
selected model's token rates.

```sh
uv run python -m inbound_voice.report runs
```

From the repository root, the equivalent command is:

```sh
uv run --project voice python -m inbound_voice.report voice/runs
```

The report prints p50, p95, and max percentiles for both latency measurements and
estimated cost across calls. An asterisk marks a partial estimate where usage or a price
is unknown. Edit `config/prices.yaml` only when you have an official current source;
prices are documented in USD and exclude applicable taxes and other charges.

## Scripted A/B calls

Run the scripted caller harness from this directory:

```sh
uv run python -m eval.run_matrix \
  --stt assemblyai --tts speechify --scenario employment --reps 1
```

The harness synthesizes missing caller utterances with Speechify Simba 3.2 using each
scenario's caller voice, caches them under `eval/audio/`, connects an aiortc caller to the
SmallWebRTC runner, records agent audio and a caller-side timeline, and scores each call
against the scenario checks. `results.json` and the per-call plus aggregate `summary.md`
are written under `eval/results/<UTC timestamp>/`; call artefacts, including `bot_audio.wav`
and `caller_timeline.json`, are copied into `runs/<call_id>/`.
Select multiple providers, scenarios, or repetitions by listing values after each option.
The matrix defaults to Speechify `beatrice_32` and ElevenLabs Katie X
(`MzqUf1HbJ8UmQ0wUsx2p`); override them with `--speechify-voice` and
`--elevenlabs-voice`.

Caller audio is synthetic TTS, band-limited to 8 kHz and upsampled for WebRTC; it is not a
substitute for real UK callers or representative real-world speech.

## Business prompt, readback, and caller ID

Call behavior belongs in each assistant's multiline `instructions` setting, not in generic
prompt code. The `uk_law_firm` template supplies the Demo & Co behavior; the renderer adds
identity, configured business data, knowledge, and only the mechanics enabled by settings.
The channel-neutral business context, intake extraction, deadline calculation, and pricing
helpers are shared through `inbound_core`; voice-only readback, caller identity, and call
control remain in `inbound_voice`.
Set `readback` to `off`, `email`, or `critical`. The law-firm template uses `email`.
Email read-backs spell addresses as separate letters and spoken words such as “dot” and
“at,” rather than punctuation symbols; common domains are spoken as words.

For Twilio calls, the caller number is read from the start message's `From` custom parameter.
Local/evaluation WebRTC calls may set `CALLER_ID_OVERRIDE`; it is test-only and must not be
used as a production caller identity. When caller ID is present it is merged into the service's
phone field with `source: caller_id` and confidence `1.0`; a different number stated by the
caller takes precedence and retains caller ID as an alternative.

Incomplete UK phone numbers hold user-turn finalization for up to `NUMBER_HOLD_SECS`
(default `2.0`). Caller speech resuming within the hold continues the same turn; otherwise the
turn proceeds when the hold expires. Holds and their actual duration/resumption are recorded
in `metrics.json` and the scripted caller timeline.
