# Text channels

The WhatsApp adapter normalizes Meta webhook messages into the shared tenant-scoped
`Conversation` and `Message` model. A business connects a WhatsApp phone-number ID to an
assistant. The signed webhook resolves that connection with the tenant-safe
`whatsapp_route` database function, stores each inbound message under its `wamid`, and
enqueues a durable reply job. Duplicate webhook deliveries are acknowledged without
creating another message or job.

The text agent renders the assistant's editable business prompt, policies, prices, hours,
services, fields, the latest knowledge digest, and a lookup-only contact context card from the
same shared core as voice. Its first reply discloses that it is the business's AI assistant;
later replies do not repeat the disclosure or copy the spoken call greeting. Generic WhatsApp
rules keep replies plain-text and concise. The assistant's business behavior remains controlled
by its template and editable configuration; channel code does not add vertical-specific offers.
Burst messages are coalesced so only the latest inbound message in a thread triggers a reply.
Retries skip a send when an assistant message already follows the latest inbound message.
After a quiet period, extraction runs over the thread and persists the summary, intake,
follow-up tasks, knowledge gaps, and contact-graph ingestion.

## Configuration

Backend environment variables:

| Variable | Default / use |
|---|---|
| `WHATSAPP_MODE` | `fake` by default; use `graph` for Meta Cloud API |
| `WHATSAPP_APP_SECRET` | Signs webhook payloads and simulator requests |
| `WHATSAPP_VERIFY_TOKEN` | Shared token used for Meta webhook verification |
| `WHATSAPP_ACCESS_TOKEN` | Bearer token for Graph sends |
| `WHATSAPP_GRAPH_BASE_URL` | `https://graph.facebook.com/v26.0` |
| `TEXT_AGENT_MODEL` | `gemini-3.5-flash-lite` |
| `EXTRACTION_MODEL` | `gemini-3.8-flash` |
| `MESSAGE_EXTRACT_DELAY_SECONDS` | `120` |
| `FAKE_OUTBOX_PATH` | `backend/var/whatsapp-outbox.jsonl` |
| `PRICE_TABLE_PATH` | Repository `voice/config/prices.yaml`; missing files leave message cost unknown |
| `GOOGLE_API_KEY` | Required by the text-agent and extraction jobs |

The Graph base URL pins the sender to Graph API **v26.0** (the latest release we selected
for this implementation; Meta's versioned changelog is
<https://developers.facebook.com/docs/graph-api/changelog/>). Production startup requires
`WHATSAPP_MODE=graph`, an app secret, and an access token. The current sender uses one
global access token; per-connection tokens and embedded signup are later work.

Run the API and worker, set `WHATSAPP_MODE=fake`, then add a WhatsApp channel connection
using the `phone_number_id` from Meta Business settings. The simulator sends signed Meta
payloads to a local backend and waits for its fake JSONL outbox response:

```sh
uv run --directory backend python -m inbound_api.channels.simulate whatsapp \
  --phone-number-id 123456789012345 \
  --from 447700900123 \
  --name "Alex Example" \
  --text "What are your opening hours?"
```

## Session window and current limits

An active WhatsApp conversation is scoped to the receiving phone-number ID and contact `wa_id`;
it is reused while its last message is less than 24 hours old. After that it is completed and a
new conversation is created. Each inbound message uses its UTC, microsecond-resolution server
receipt time for message ordering and records it in `received_at`; the original Meta timestamp
is retained separately. Assistant message timestamps record the send time for received-to-sent
latency.
Replies outside Meta's 24-hour customer-service window are not sent; the inbox receives a
`whatsapp_template_needed` task instead. Approved template sends are not implemented yet.

Media is not downloaded or transcribed. Non-text messages are stored as placeholders such as
`[image]` or `[audio]`, with the original message type in metadata. The simulator and fake
sender are for development; graph-mode sends use the configured Cloud API bearer token.
