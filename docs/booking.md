# Google Calendar appointment booking

Appointment booking is available to voice and WhatsApp assistants. It checks real free/busy
data, offers configured appointment slots, and creates a Google Calendar event only after a
customer selects and confirms an offered slot. It does not reschedule or cancel events.

## Configure an assistant

Enable Calendar booking in the assistant's Booking settings and add at least one appointment
type. Each type has a lowercase key, label, duration, usage guidance, and optional location or
meeting link. Configure minimum notice, booking horizon, slot step, buffers, the number of
offered slots, and Calendar attendee updates. Also enable both `check_availability` and
`book_appointment` in Live actions. Booking configuration does not automatically enable actions.

Connect Calendar from the assistant's Channels section. Google Calendar uses the existing Google
OAuth connection with Calendar scopes; the Calendar deployment mode must be enabled, and Google
Calendar mode requires Gmail Google mode. The fake provider is available for local development.
Production must use Google or disable Calendar; fake mode is not allowed.

## Google Cloud setup (needs Vincent)

Enable the Google Calendar API in the Google Cloud project. Add the `calendar.events` and
`calendar.freebusy` scopes to the OAuth consent screen. Existing Gmail connections must reconnect
with **Connect Google Calendar** to grant Calendar access; reconnecting preserves the Gmail feature
and adds Calendar access.

## Booking safeguards

- Availability is generated in the assistant's configured timezone and accounts for weekdays,
  daylight-saving transitions, appointment duration, existing events, configured buffers, and
  optional customer preferences.
- Slots use stable UTC IDs. A booking request must refer to an ID previously offered to that
  caller; the backend rechecks availability under a transaction lock before creating the event.
- Database booking records are idempotent. If persistence fails after the Calendar event is
  created, the event is cancelled as compensation.
- Voice uses prefetched availability where available; WhatsApp uses Gemini function calling
  for no more than three tool rounds. Gmail drafts never receive booking tools.
- Successful bookings create a system conversation message, an appointment commitment, and
  booking details in conversation metrics.

For voice calls, the system speaks configurable holding messages while checking availability
or creating a booking. The model calls each tool immediately and silently, avoiding dead air or
an announcement without the corresponding action. These voice-only messages do not affect
WhatsApp or other text conversations. This is intentional: Gemini has been unreliable when asked
to speak and call a tool in the same turn, sometimes producing a silent call or an announcement
without a call.

The booking handler returns the tool result as soon as the Calendar operation finishes; it does
not wait for the holding message to finish playing. The output transport orders the result behind
audio already queued, and a background marker wait logs whether the holding message reached the
transport, with a 10-second timeout.

## Local testing

Use `CALENDAR_MODE=fake` and the `CALENDAR_FAKE_PATH` state file (by default
`backend/var/calendar-fake.json`). Set `CALENDAR_FAKE_DELAY_MS` to simulate provider latency; the
benchmark and fake Calendar evaluation use a 300 ms delay. Run the latency benchmark with
`uv run --directory backend python ../scripts/booking_latency.py`. Run the booking voice scenario with:

```sh
cd voice && uv run python -m eval.run_matrix --transport twilio --fake-api --scenario booking
```

## Latency results

Measured with `scripts/booking_latency.py` (30 requests per row, through the real FastAPI app over
ASGI with a local Postgres; p50 is the median and p95 is the sorted value at index
round(0.95 × (n − 1))). Both providers simulate a 300 ms provider
round trip: the fake uses `CALENDAR_FAKE_DELAY_MS=300` and Google uses an `httpx.MockTransport`
that sleeps 300 ms per request. Booking makes two provider calls (free/busy re-check plus event
insert), which is why it costs about twice as much as availability.

| Provider | Endpoint | p50 ms | p95 ms | max ms |
|---|---|---:|---:|---:|
| fake (300 ms delay) | `/internal/voice/availability` | 306.5 | 312.2 | 370.4 |
| fake (300 ms delay) | `/internal/voice/book` | 625.1 | 628.1 | 637.2 |
| Google (MockTransport, 300 ms) | `/internal/voice/availability` | 305.7 | 309.8 | 315.0 |
| Google (MockTransport, 300 ms) | `/internal/voice/book` | 623.0 | 628.7 | 629.1 |

During a call, `check_availability` runs locally on the free/busy prefetched at call start: p50
0.76 ms, p95 0.83 ms. Timings against real Google Calendar and real STT/TTS/LLM providers have not
been measured yet.

## WhatsApp

WhatsApp has no spoken holding message. Offered slots are stored in the assistant reply's
`meta.tool_calls` results. When a later inbound message starts a new `message_reply` job, the job
rebuilds those slot IDs using the configured appointment types and adds the most recent offer to
the prompt history as `Note: slots offered — <slot_id>: <text>; ...`. The customer can therefore
choose a slot in a later message, and only an earlier offered slot can be booked.

See [voice pipeline](voice-pipeline.md) and [text channels](text-channels.md) for channel
behavior. `.env.example` documents Calendar mode, fake-file path, and fake-provider delay.
