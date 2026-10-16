# Inbound voice prototype (M0)

This prototype runs an inbound UK law-firm voice agent with browser WebRTC as the primary
test path and a Twilio Media Streams transport for later phone-number testing.

## Setup

Install [uv](https://docs.astral.sh/uv/) and Python 3.12. From this directory:

```sh
uv sync
cp .env.example .env
```

Set `OPENROUTER_API_KEY` and `DEEPGRAM_API_KEY` in `.env`. The demo firm, fees, and price
table are illustrative. `LLM_MODEL` defaults to `google/gemini-2.5-flash`; `TTS_VOICE`
defaults to `aura-2-pandora-en` and can be set to `aura-2-draco-en`.

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

Each session writes `transcript.jsonl`, `intake.json`, `tasks.json`, and `metrics.json` to
`voice/runs/<call_id>/`. That directory is ignored by Git. Call-level metrics include
VAD-stop-to-first-bot-speaking and Smart-Turn-end-to-first-bot-speaking latency for each
turn, Pipecat service latency and usage measurements, and estimated cost. Deepgram
streaming STT cost is based on the full call duration because the audio stream stays open
for the call; measured STT audio seconds are retained as informational usage data.

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
