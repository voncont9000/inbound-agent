# Voice pipeline (M0)

## Architecture

```text
Browser SmallWebRTC / Twilio Media Streams
                  │
                  ▼
           Pipecat transport
                  │
                  ▼
 Deepgram Nova-3 STT (en-GB + legal keyterms)
                  │
       Silero VAD + local Smart Turn v3
                  │
                  ▼
 OpenRouter LLM (configurable model, function tools)
                  │
                  ▼
 Deepgram Aura-2 TTS (British voice)
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

## Vendor choices and seams

- **Deepgram** supplies streaming Nova-3 speech recognition and Aura-2 speech synthesis.
  `DEEPGRAM_API_KEY`, `TTS_VOICE`, and the service classes are isolated in the bot.
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

## Latency and cost

The latency target is **p95 ≤ 1.5 seconds** from when the caller stopped speaking (VAD stop time
minus its `stop_secs` confirmation silence) to the first `BotStartedSpeakingFrame` from the transport output. M0 also records the separate
Smart-Turn-end-to-first-bot-speaking interval. It keeps only the latest pending caller
turn, clears it on interruption, and reports p50, p95, and max for both intervals. Other
measurements include Pipecat STT/LLM/TTS TTFB-family metrics, token usage, synthesized
characters, and call duration.

Estimated cost uses `voice/config/prices.yaml`: Deepgram Nova-3 streaming STT per minute
of full call duration (the stream remains open for the call), Aura-2 TTS per 1,000
characters, OpenRouter LLM input/output per million tokens, and UK local Twilio inbound
per minute. Measured STT audio seconds remain in usage metrics for information but do not
determine STT cost. The table records official source URLs and the 2026-10-16 check date.
Missing price or usage components are reported as unknown, not silently treated as a
zero-cost service. Twilio number rental and taxes are excluded.

## Deferred

M0 writes local files only. Google Sheets and email sinks, the customer context graph,
multi-tenancy, persistence/retention controls, practice-management connectors, and
production operations are later work. Demo fees are fictional and must be replaced with
firm-approved prices before any real client-facing use.
