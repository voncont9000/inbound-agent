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
definitions, safeguards, urgency rules, and enabled live actions. Assistant files override
template values using recursive dictionary merging; lists and scalar values replace the
template values. Service fields and actions are data-driven so a future settings UI can
edit the same configuration shape.

Critical fields are confirmed during the call with `confirm_critical_fields`; other intake
details are extracted after the call. `intake.live.json` records caller-confirmed values,
`intake.post_call.json` records extracted values, and merged `intake.json` preserves
provenance, confidence, alternatives, and `needs_review` flags for conflicts, invalid
values, or confidence below the configured threshold.

The assistant speaks the configured `language` (the UK law-firm template uses British
English). The prompt acknowledges volunteered details, asks critical questions naturally,
returns once to unanswered critical fields, and avoids repeating the same question twice
in a row. Before ending, it reads back collected unconfirmed critical details together and
waits for confirmation; enabled confirmation and end-call actions are then used in order.

Re-run extraction and the live/post-call merge for an existing call without starting a
runner:

```bash
uv run python -m inbound_voice.extraction --run-dir runs/<call_id> [--config config/assistants/demo_firm.yaml]
```

The command reads `transcript.jsonl` and `intake.live.json`, rewrites
`intake.post_call.json` and `intake.json`, and recalculates the extraction usage/cost in
`metrics.json` when that file exists.

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

`SMART_TURN_STOP_SECS` defaults to `2.0` seconds. Pipecat uses this silence timeout when
Smart Turn judges an utterance incomplete; the first scripted smoke call fell back to its
3-second default on four of eight turns, adding about 3.2 seconds before turn end.

The evaluation runner accepts `--smart-turn-stop-secs` to set and record this value for
each runner; compare settings with `uv run python -m eval.run_matrix --smart-turn-stop-secs
1.0 ...`.

The template enables only `end_call` as a live action by default. Other actions can be
enabled per assistant. Caller-final keyword urgency rules remain live regardless of action
settings; configured date-deadline checks, callback requests, and complaints also become
post-call tasks from extraction. If caller and assistant exchange a goodbye but no
`end_call` tool arrives, the bot ends the call 1.5 seconds after speaking finishes unless
the caller starts speaking again.

The baseline turn-stage medians at a 2-second Smart Turn setting were: employment —
caller stop→turn-end **2.200 s**, turn-end→LLM first token **0.276 s**, LLM first
token→TTS first audio **0.399 s**; conveyancing — **0.305 s**, **0.170 s**, and
**0.309 s** respectively. These were one call per scenario; matrix summaries now include
the stage medians and LLM TTFB.
The evaluation runner accepts `--smart-turn-stop-secs` to set and record this value for
each runner; compare settings with `uv run python -m eval.run_matrix --smart-turn-stop-secs
1.0 ...`.

The template enables only `end_call` as a live action by default. Other actions can be
enabled per assistant. Caller-final keyword urgency rules remain live regardless of action
settings; configured date-deadline checks, callback requests, and complaints also become
post-call tasks from extraction. If the caller and assistant exchange a goodbye but no
`end_call` tool arrives, the bot ends the call 1.5 seconds after speaking finishes unless
the caller starts speaking again.

The baseline turn-stage medians at a 2-second Smart Turn setting were: employment —
caller stop→turn-end **2.200 s**, turn-end→LLM first token **0.276 s**, LLM first
token→TTS first audio **0.399 s**; conveyancing — **0.305 s**, **0.170 s**, and
**0.309 s** respectively. These were one call per scenario; matrix summaries now include
the stage medians and LLM TTFB.

## Browser web call

```sh
uv run --env-file .env bot.py
```

Open the local runner's URL (normally `http://localhost:7860`) and select **WebRTC** to
start a browser call. The agent greets the caller when the client connects. When it
finishes a conversation with `end_call`, it speaks its goodbye before ending the pipeline.
The page is served by Pipecat's development runner. Real speech processing requires the
API keys.

## Twilio Media Streams

Configure `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN`, then expose the local runner using
a secure tunnel such as ngrok:

```sh
ngrok http 7860
uv run --env-file .env bot.py -t twilio -x YOUR_NGROK_HOST
```

Configure the Twilio number's incoming voice webhook to the runner's generated HTTP URL.
The runner returns TwiML that connects the call to its WebSocket media endpoint; the
equivalent TwiML shape is:

```xml
<Response>
  <Connect>
    <Stream url="wss://YOUR_NGROK_HOST/ws" />
  </Connect>
</Response>
```

Use a public HTTPS/WSS endpoint and validate the webhook in any production deployment.
Twilio credentials are optional for local WebRTC and must never be committed.

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
