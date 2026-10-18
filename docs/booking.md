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

See [voice pipeline](voice-pipeline.md) and [text channels](text-channels.md) for channel
behavior. `.env.example` documents Calendar mode, fake-file path, and fake-provider delay.
