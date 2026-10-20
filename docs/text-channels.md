# Text channels

Completed conversation actions are configured and delivered as described in
[After-conversation actions](actions.md).

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
follow-up tasks, knowledge gaps, and contact-graph ingestion. Configured plain-sentence
urgency rules are assessed during that existing extraction call and can create urgent tasks
with the model's reason and evidence.

The shared AI-disclosure setting controls WhatsApp: `at_start` adds a one-time first-reply
disclosure (the default), while `when_asked` tells the assistant not to volunteer it and to
answer honestly when asked. Gmail drafts receive no disclosure instruction because a human
reviews and sends them.

## Response language

Assistant language is stored as a locale code: `en-GB`, `en-US`, `de-DE`, `es-ES`, `es-MX`,
`fr-FR`, `it-IT`, `pt-BR`, or `multi`. The default is `en-GB`. Legacy human-readable values
are normalized when configurations load; unknown values also fall back to `en-GB`.
WhatsApp and Gmail use a written-language instruction derived from the same assistant locale.
`multi` asks text agents to reply in the language the customer writes in. Phone prompts use a
separate spoken-language instruction and do not leak voice-specific instructions into text
responses.

## Configuration

Backend environment variables:

| Variable | Default / use |
|---|---|
| `WHATSAPP_MODE` | `fake` by default; use `graph` for Meta Cloud API or `off` to disable |
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
<https://developers.facebook.com/docs/graph-api/changelog/>). In production, `WHATSAPP_MODE=off`
is the default and needs no Meta credentials. `fake` is rejected; `graph` requires an app secret,
verify token, and access token. The current sender uses one global access token; per-connection
tokens and embedded signup are later work.

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

Each WhatsApp reply and Gmail draft stores its token usage and estimated cost in the assistant
message's `meta`, and adds the cost to `conversations.estimated_cost` (running total plus
per-component sums). The Home dashboard reports that total as AI cost
([m1-architecture.md](m1-architecture.md#home-dashboard)).

## Session window and current limits

An active WhatsApp conversation is scoped to the receiving phone-number ID and contact `wa_id`;
it is reused while its last message is less than 24 hours old. After that it is completed and a
new conversation is created. Each inbound message uses its UTC, microsecond-resolution server
receipt time for message ordering and records it in `received_at`; the original Meta timestamp
is retained separately. Assistant message timestamps record the send time for received-to-sent
latency.
Replies outside Meta's 24-hour customer-service window are not sent; the inbox receives a
`whatsapp_template_needed` task instead. Approved template sends are not implemented yet.

When Calendar booking is configured and the assistant has both booking live actions enabled,
WhatsApp replies can check availability and book a confirmed slot. Tool calls are limited to
three Gemini rounds. Offered slot IDs are stored in assistant-message tool-call metadata and
restored into later message-reply jobs, so a customer can choose and book a slot in a separate
message. Only returned slot IDs may be booked, and booking/tool timing details are stored with
conversation metrics. WhatsApp has no spoken holding message. Without a connected Calendar
context, the existing plain-text reply path is unchanged. Gmail drafts never receive booking
tools.

## Gmail (draft only)

Gmail uses Gmail History API plus Pub/Sub push notifications and periodic reconciliation.
The worker parses full Gmail messages, skips automated/newsletter mail, and treats same-domain
senders as internal only when the mailbox domain is not a public mail domain, resolves contacts
by email, and classifies each thread. Enquiries
create a threaded Gmail draft and an in-app assistant message with a “Draft in Gmail”
badge. Needs-human messages get an `AI/Needs human` label and task; spam and unrelated
messages are labelled and completed. **The service never sends email and never attaches
files to a reply.** A human reviews and sends drafts.

The assistant's editable `email_triage` configuration defines what counts as `enquiry`,
`needs_human`, `spam`, and `not_relevant`; these definitions are included in the classifier
prompt instead of being hard-coded business policy. When uncertain whether a message is
spam or not relevant rather than an enquiry or a request for human attention, the classifier
is instructed to choose `needs_human`. It returns a short staff-facing reason with the
category. Needs-human tasks use that reason (or “Email requires human review” if it is
missing), and every category and reason is stored in `conversation.metrics.email_triage`.
The conversation detail page displays the saved category and reason.

Gmail channel settings may include an `include_rules` list. Rules are OR-matched case-insensitive
substrings over the full From header, Subject, or recipient headers. A `to` rule checks `To`,
`Cc`, `Delivered-To`, `X-Original-To`, `X-Forwarded-To`, and `Envelope-To`, which covers
messages forwarded from a custom domain into a Gmail inbox. With no rules, existing message
handling is unchanged; when rules are configured and none match, the message is skipped,
left in Gmail, and labelled `AI/Skipped`.

OAuth uses `openid email https://www.googleapis.com/auth/gmail.modify`. `gmail.modify`
is a restricted Google scope, but is the narrowest single Gmail scope that supports
reading messages/history, creating drafts, and applying labels. Production use requires
Google restricted-scope verification and any associated security assessment required
for the deployed application. Refresh tokens are Fernet-encrypted in
`channel_connections.credentials_ciphertext`; they are never stored in connection
settings or returned by the API.

### Google features

Gmail OAuth is the shared Google sign-in for connected Google features. The default
request includes Gmail access; callers may additionally request the `sheets` or
`calendar` feature. Granted scopes are stored internally and connection responses expose
only derived feature names. Gmail uses `gmail.modify`, Sheets uses `spreadsheets`, and
Calendar uses `calendar.events` plus `calendar.freebusy`.

Deployments may set `GMAIL_MODE=off` before a Google project is available. In that mode
the Gmail webhook returns 503, OAuth start reports that Gmail is not enabled, and queued
Gmail sync jobs are no-ops. Production accepts `off` without Google configuration;
`google` requires the OAuth client, Pub/Sub audience/topic/service account, and encryption
key. Production does not allow the local `fake` mode.

### Google Cloud setup

1. Create a Google Cloud project and configure the OAuth consent screen.
2. Create an OAuth client ID, register the callback URL from
   `GOOGLE_OAUTH_REDIRECT_URL`, and configure the client ID and secret.
3. Complete verification for the restricted `gmail.modify` scope before production use.
4. Create a Pub/Sub topic and grant
   `gmail-api-push@system.gserviceaccount.com` permission to publish to it.
5. Create a push subscription targeting `POST /webhooks/gmail`, enable OIDC
   authentication, set the token audience to `GMAIL_PUBSUB_AUDIENCE`, and configure
   the expected service-account email in `GMAIL_PUBSUB_SERVICE_ACCOUNT`.
6. Set the OAuth, Pub/Sub, and Fernet `CREDENTIALS_ENCRYPTION_KEY` values in backend
   deployment secrets.

The fake provider stores Gmail API `format=full` JSON under `GMAIL_FAKE_DIR`; use
`GMAIL_PUSH_TOKEN` for simulator authentication:

```sh
uv run --directory backend python -m inbound_api.channels.simulate gmail \
  --mailbox assistant@example.test --from "Alex <alex@example.test>" \
  --subject "Question" --text "Could you explain the process?"
```

Text, Markdown, CSV, PDF, and DOCX attachments up to 2 MB use the existing
knowledge-file reader and are included as context, capped at 4,000 characters each.
Other types and larger files are ignored. Outgoing drafts never include attachments.
Extraction sees each customer email as `From:`/`Subject:` header lines (plus `Reply-To:` when it
differs from the sender) followed by the body and any attachment text, so the sender's name and
address can be captured with quoted evidence.
FR-E5 style learning and FR-E7 suggested mailbox rules are out of scope.

Media is not downloaded or transcribed. Non-text messages are stored as placeholders such as
`[image]` or `[audio]`, with the original message type in metadata. The simulator and fake
sender are for development; graph-mode sends use the configured Cloud API bearer token.
