# Voice pipeline (M0)

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
 OpenRouter LLM (configurable model, function tools)
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
storage; Google Sheets and email sinks are intentionally deferred.

## Provider choices and seams

The speech providers are selected independently through `STT_PROVIDER` and `TTS_PROVIDER`.
The provider-specific Pipecat service construction is isolated in
`inbound_voice/providers.py`; the LLM remains OpenRouter.

| Component | Provider | Model/configuration | Voice/key |
|---|---|---|---|
| STT | AssemblyAI | Universal-3.6 Pro Realtime; legal keyterms | `ASSEMBLYAI_API_KEY` |
| STT | Soniox | `stt-rt-v5`; English hint and legal context terms | `SONIOX_API_KEY` |
| STT | Deepgram | Nova-3, `en-GB`, legal keyterms | `DEEPGRAM_API_KEY` |
| LLM | OpenRouter | `LLM_MODEL`, default `google/gemini-2.5-flash` | `OPENROUTER_API_KEY` |
| TTS | Speechify | Simba 3.2 | `SPEECHIFY_API_KEY`; optional `TTS_VOICE`, default `beatrice_32` (en-GB) |
| TTS | ElevenLabs | `ELEVENLABS_MODEL`, default `eleven_v4_turbo` | `ELEVENLABS_API_KEY`; required `TTS_VOICE` ID |
| TTS | Deepgram | Aura-2 | `DEEPGRAM_API_KEY`; optional `TTS_VOICE`, default `aura-2-pandora-en` |

- **Speech providers** use separate API keys, except Deepgram uses one key for either
  selected side. `OPENROUTER_API_KEY` is always required.
- **OpenRouter** provides a common OpenAI-compatible interface to an environment-selected
  model. `LLM_MODEL` defaults to `google/gemini-2.5-flash`; replacing the LLM service does
  not affect the turn or sink interfaces.
- **Pipecat** owns real-time orchestration, WebRTC/Twilio transports, service metrics, and
  interruption handling. A firm config and intake tool layer keep product policy apart
  from vendor-specific adapters.

The bot queues an `LLMRunFrame` when a client connects so the system-prompt greeting is
spoken first. Client disconnects cancel the pipeline task. The `end_call` tool records its
result before sending `EndWorkerFrame` upstream, allowing the spoken goodbye to finish before
the pipeline ends.

## Turn-taking and safeguards

Silero VAD detects speech activity and Pipecat's local Smart Turn v3 analyzer predicts
when the caller has finished a turn. Interruptions are enabled. The prompt discloses AI
use, prevents legal advice and client-status disclosure, constrains prices to the demo
fee table, and directs danger, urgency, complaints, and callback requests to explicit
tools. Intake is validated against separate conveyancing and employment Pydantic presets.
Employment unfair-dismissal deadlines are calculated in code from the effective
termination date and flagged urgent at 28 days or less.

`SMART_TURN_STOP_SECS` controls Pipecat's silence fallback when Smart Turn marks an
utterance incomplete; it defaults to `2.0` seconds. The first scripted smoke call used
Pipecat's 3-second default on four of eight turns, adding about 3.2 seconds before turn end.

## Latency and cost

The latency target is **p95 ≤ 1.5 seconds** from when the caller stopped speaking (VAD stop time
minus its `stop_secs` confirmation silence) to the first `BotStartedSpeakingFrame` from the transport output. M0 also records the separate
Smart-Turn-end-to-first-bot-speaking interval. It keeps only the latest pending caller
turn, clears it on interruption, and reports p50, p95, and max for both intervals. Other
measurements include Pipecat STT/LLM/TTS TTFB-family metrics, token usage, synthesized
characters, and call duration.

Estimated cost uses provider-specific STT per-minute rates multiplied by full call
duration (the stream/session remains open for the call), provider-specific TTS rates per
1,000 synthesized characters, model-specific OpenRouter input/output rates per million
tokens, and UK local Twilio inbound per minute. Measured STT audio seconds remain in usage
metrics for information but do not determine STT cost. `prices.yaml` records vendor
sources and check dates; older metrics without provider metadata default to Deepgram and
Gemini 2.5 Flash.
Missing price or usage components are reported as unknown, not silently treated as a
zero-cost service. Twilio number rental and taxes are excluded.

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

After adding the generated intake schema, explicit tool-use instructions, and a
`SMART_TURN_STOP_SECS=2.0` default, the same AssemblyAI + Speechify employment scenario
reported headline VAD-stop-to-audio p50 **2.45 s** / p95 **3.99 s**; turn-end-to-audio
p50 **1.21 s**; **4/12** checks; estimated cost **$0.029**. `end_call` fired, but
`save_intake` still did not: `intake.json` had no practice area or fields, and no urgent
task was recorded. Four of eight turns had a ~2.2-second VAD-stop-to-turn-end gap,
down from ~3.2 seconds in the first run.

The harness plays scripted utterances in a fixed order regardless of the agent's question,
so a caller's next answer can be misaligned with what the agent asked.

## Deferred

M0 writes local files only. Google Sheets and email sinks, the customer context graph,
multi-tenancy, persistence/retention controls, practice-management connectors, and
production operations are later work. Demo fees are fictional and must be replaced with
firm-approved prices before any real client-facing use.

Scripted A/B calls use synthetic TTS caller audio band-limited to 8 kHz; this is not a
substitute for real UK callers.
