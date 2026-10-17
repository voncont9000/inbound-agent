# Inbound voice prototype (M0)

This prototype runs an inbound UK law-firm voice agent with browser WebRTC as the primary
test path and a Twilio Media Streams transport for later phone-number testing.

## Setup

Install [uv](https://docs.astral.sh/uv/) and Python 3.12. From this directory:

```sh
uv sync
cp .env.example .env
```

Set `OPENROUTER_API_KEY` and the API key for each selected speech provider in `.env`.
`OPENROUTER_API_KEY` is always required. The default selection is
`STT_PROVIDER=assemblyai` and `TTS_PROVIDER=speechify`, requiring
`ASSEMBLYAI_API_KEY` and `SPEECHIFY_API_KEY`. Use `SONIOX_API_KEY` for Soniox,
`ELEVENLABS_API_KEY` for ElevenLabs, and `DEEPGRAM_API_KEY` when either provider is
Deepgram. The demo firm, fees, and price table are illustrative.

## Selecting providers and voices

Change `STT_PROVIDER` and `TTS_PROVIDER` in `.env`, then restart the runner to A/B providers.
Supported STT values are `assemblyai`, `soniox`, and `deepgram`; supported TTS values are
`speechify`, `elevenlabs`, and `deepgram`. The LLM stays on OpenRouter:
`LLM_MODEL` defaults to `google/gemini-2.5-flash`.

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

Each session writes `transcript.jsonl`, `intake.json`, `tasks.json`, `tool_calls.jsonl`, and
`metrics.json` to `voice/runs/<call_id>/`. That directory is ignored by Git. Call-level
metrics include
VAD-stop-to-first-bot-speaking and Smart-Turn-end-to-first-bot-speaking latency for each
turn, selected STT/TTS providers and LLM model, Pipecat service latency and usage
measurements, and estimated cost. STT cost is based on the full call duration because the
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
