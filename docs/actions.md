# After-conversation actions

After-conversation actions are configured per assistant in `AssistantConfig.post_actions`.
They run asynchronously after the voice or text conversation results have been persisted.
The feature is channel- and service-filterable and does not contain vertical-specific
behavior.

```yaml
post_actions:
  - kind: notify_staff
    key: staff-alert
    enabled: true
    trigger:
      events: [urgent, callback_requested]
      channels: [phone, whatsapp]
      services: [conveyancing]
    to: [team@example.com]
    subject_template: "New {{service}} enquiry from {{contact.name}}"
    body_template: |-
      {{summary}}

      {{intake_table}}
  - kind: email_customer
    key: customer-confirmation
    enabled: false
    trigger:
      events: [conversation_completed]
    subject_template: "Thanks for contacting {{business_name}}"
    body_template: "Hello {{contact.name}}, we will be in touch."
```

Action keys are unique within an assistant. Trigger event lists are required and deduplicated.
An empty channel or service list means all channels or services. `notify_staff` requires one or
more validated recipient email addresses; `email_customer` always addresses the known customer
email and cannot configure an arbitrary recipient. Sheets columns are ordered and each contains
a header and value template. A new Sheets action starts with Started (`{{started_at}}`), Name
(`{{contact.name}}`), Channel (`{{channel}}`) and Summary (`{{summary}}`), which can be renamed
or removed. Webhook URLs must be HTTP(S); production delivery requires HTTPS.
In web settings, choose action types from cards, add multiple actions independently, and use
**Insert variable** in message and column templates instead of typing placeholder braces. A
Google Sheets action accepts either a spreadsheet ID or a pasted Google Sheets URL.

## Events

| Event | Derived when |
|---|---|
| `conversation_completed` | The conversation results are persisted |
| `urgent` | A non-dismissed `urgent` task exists |
| `callback_requested` | A non-dismissed `callback` task exists |
| `needs_human` | A non-dismissed `needs_human` or `email_needs_human` task exists |
| `complaint` | A non-dismissed `complaint` task exists |

Test calls are included in planning and delivery like other conversations. Email subjects are
prefixed with `[Test] `, and webhook bodies include a top-level `test` boolean. The
`test_call` template variable is true for browser test calls. Runs are unique per conversation
and action key. The planner records pending events before enqueueing execution, so duplicate
planning jobs do not send again. If a later planning pass discovers a new trigger event, it adds
only that event and creates a new fire count. Webhook event IDs include the run ID and fire count
so consumers can deduplicate deliveries.

## Template variables

The owner settings page fetches the available variables from `GET /post-actions/variables`.
They include `business_name`, `contact.name`, `contact.email`, `contact.phone`, `channel`,
`started_at`, `service`, `service_key`, `intake.<field_name>`, `intake_table`, `summary`,
`urgency`, `urgency_reasons`, `tasks`, `events`, `test_call`, `conversation.id`, and
`conversation.link`. Unknown placeholders render as an empty string. Subject line CR/LF
characters are sanitized.
Substituted Google Sheets cell values that begin with spreadsheet formula characters are
escaped before being written.

## Providers and environment

Development and tests default to fake providers that append rendered records to JSONL files in
`POST_ACTIONS_FAKE_DIR` (`backend/var/post-actions`):

| Setting | Values / purpose |
|---|---|
| `EMAIL_SENDER` | `off`, `fake`, `gmail`, or `resend`; default `fake` |
| `RESEND_API_KEY`, `EMAIL_FROM` | Required by the Resend email sender |
| `RESEND_BASE_URL` | Defaults to `https://api.resend.com` |
| `SHEETS_WRITER` | `off`, `fake`, or `google`; default `fake` |
| `WEBHOOK_SENDER` | `off`, `fake`, or `http`; default `fake` |
| `POST_ACTIONS_FAKE_DIR` | Fake email, Sheets, and webhook outbox directory |
| `POST_ACTION_WEBHOOK_ALLOW_PRIVATE_URLS` | Defaults to false; allow local/private webhook targets only in dev/test |
| `APP_BASE_URL` | Base URL used for conversation links |

For after-conversation email and Sheets actions, the providers first use the assistant's active
Gmail connection when it has the required feature, then fall back to the newest eligible active
Gmail connection in the same workspace. A connection from another workspace is never used.
This fallback is specific to after-conversation actions; Calendar booking and inbox access remain
assistant-scoped. An unassigned Gmail connection remains eligible for these workspace-scoped send
and Sheets actions after its assistant is deleted. It does not receive or ingest inbound mail until
reassigned. Sheets requires the Sheets feature even in fake writer mode. Resend requires
`RESEND_API_KEY` and `EMAIL_FROM`. Webhook signatures use the Fernet-encrypted signing secret
stored per assistant/action; secret values are returned only once when generated or rotated.
Production rejects fake providers, private webhook targets, and incomplete provider configuration.

## Webhooks

The version 1 request body is compact, sorted JSON with the event ID, action key, triggering
events, conversation ID, and rendered context:

```json
{
  "action_key": "staff-hook",
  "context": {"business_name": "Example"},
  "conversation_id": "00000000-0000-0000-0000-000000000000",
  "event_id": "run-uuid:1",
  "events": ["urgent"],
  "test": false
}
```

Requests include `schema_version: "1"`, `Content-Type: application/json`, a user agent,
`X-Inbound-Event-Id`, and
`X-Inbound-Signature: sha256=<hex>`. The signature is HMAC-SHA256 over the exact request body:

```python
import hashlib
import hmac

expected = "sha256=" + hmac.new(secret.encode(), request_body, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, request.headers["X-Inbound-Signature"])
```

The HTTP sender checks URL safety, pins resolved addresses, and does not follow redirects.
Private/loopback destinations are blocked unless the explicit development/test setting is enabled.
HTTP targets are rejected outside development and test. A receiver should deduplicate by
`X-Inbound-Event-Id`.

## Delivery and setup tasks

External delivery is **at-least-once**: a worker crash after an external provider accepts a
request but before the database transaction commits can repeat that send. Webhook consumers
should use the stable event ID for deduplication. Transient failures retry with the regular
worker backoff; permanent failures and skipped actions update the action run. Missing connection
or signing setup creates an `action_setup_needed` task where appropriate, while final delivery
failures create an `action_failed` task. Both task kinds include a readable title and a short
setup or failure message. A missing customer email skips the confirmation without creating a
setup task.

The owner can dry-run an action against the latest or selected conversation. Dry runs render the
recipient, subject/body, sheet row, or webhook payload without sending, writing, or recording a
run. Conversation detail shows run status, attempts, triggering events, provider output, and
errors.
