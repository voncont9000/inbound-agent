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
policies, services, typed fields, urgency rules, live actions, and a review-confidence
threshold. Dictionary values merge recursively while assistant lists and scalars replace
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
post-call tasks use `post_call`.

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

If the last caller final and the assistant's following turn both contain goodbye language,
but no `end_call` tool arrives, the goodbye detector queues `EndWorkerFrame` 1.5 seconds
after `BotStoppedSpeaking`. New caller speech cancels the timer; the end task source is
`goodbye_detected`. Separately, after bot speech and `IDLE_PROMPT_SECS` (default 8) of
caller silence, an idle prompt is queued. After a second idle interval it says “I'll end
the call now. Thank you for calling. Goodbye.” and ends the call after speech stops, with
an `end_call` task source of `idle`. Caller speech resets the timer, and neither idle
message is queued while the bot is speaking.

`SMART_TURN_STOP_SECS` controls Pipecat's silence fallback when Smart Turn marks an
utterance incomplete; it defaults to `0.7` seconds.
The matrix runner accepts `--smart-turn-stop-secs`, sets it for each runner, and records the
value in JSON results and summary rows. Its summary reports caller-stop→turn-end,
turn-end→LLM-first-token, LLM-first-token→TTS-first-audio medians, and LLM TTFB.

At the 2.0-second setting, baseline stage medians were:

| Scenario | Caller stop→turn-end | Turn-end→LLM first token | LLM first token→TTS first audio |
|---|---:|---:|---:|
| Employment (8 turns) | 2.200 s | 0.276 s | 0.399 s |
| Conveyancing (7 turns) | 0.305 s | 0.170 s | 0.309 s |

These are one baseline call per scenario, not a p95 guarantee. Lower endpointing settings
must be evaluated for interruption/cut-off risk as well as latency.

Turn-stop matrix comparison (aggregate checks, median of per-call p50, maximum per-call
p95). The 2.0s, 1.0s, and 0.5s rows are from the earlier two-call-per-scenario runs; 0.7s
is the requested rep-3 run, with one call per scenario:

| Smart turn stop | Checks | Aggregate p50 | Maximum per-call p95 |
|---:|---:|---:|---:|
| 2.0s | 41/50 | 1.684s | 3.608s |
| 1.0s | 43/50 | 1.204s | 2.992s |
| 0.5s | 42/50 | 1.481s | 2.425s |
| 0.7s | 22/25 | 1.313s | 1.915s |

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

Scripted A/B calls use synthetic TTS caller audio band-limited to 8 kHz; this is not a
substitute for real UK callers.
