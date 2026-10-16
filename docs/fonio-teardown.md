# Fonio teardown (reference product analysis)

Reverse-engineering notes on [fonio.ai](https://app.fonio.ai/), the reference product for this project. We use them as input to the [PRD](./PRD.md).

## Method and evidence levels

The analysis used [REA](https://github.com/morluto/rea) v6.5.0 (`rea-agents`) together with a logged-in Chrome session on a **test account** that had one voice assistant and one recorded web call.

| Level | Source | What it proves |
|---|---|---|
| **Observed (UI)** | Manual navigation, plus a crawler that loaded every sidebar and assistant-tab URL and saved its text and a screenshot | The feature is visible to a customer on this plan |
| **Observed (runtime API)** | XHR/fetch metadata recorded over CDP (auth headers redacted) | The endpoint was called and returned this response shape |
| **Static (bundle)** | `rea analyze-web-bundle`, `rea inspect-web-page`, `rea export-web-scripts`, then parsing the main Vite bundle | The client defines the endpoint or route. This does not prove the feature is enabled, released or working |

Claims below are tagged **[UI]**, **[API]** or **[static]**. Nothing here comes from server code. Raw captures, cookies and exported scripts were not committed to this repo.

## Architecture (inferred)

- **Frontend:** React SPA built with Vite into one ~7 MB `index-*.js` bundle. No first-party source maps. [static]
- **API:** REST under `https://app.fonio.ai/api/*`. The client declares each endpoint as `{logName, permissions[], method, url, params, payload, query, response}` with zod schemas. 457 endpoint definitions were extracted. [static]
- **Authorization:** permission-based RBAC (`AGENT_MANAGE`, `CONTACT_*`, `KNOWLEDGEBASE_*`, `WHATSAPP_MANAGE`, `WORKSPACE_CONNECTION_*`, `TASK_*`, `BILLING_MANAGE`, …) plus separate internal-admin permissions. Auth options are password, magic link, 2FA and mobile login. [static]
- **Third parties referenced in the client:** Twilio (numbers, regulatory bundles), ElevenLabs and Inworld (TTS), Soniox (STT), Meta/Facebook SDK (WhatsApp embedded signup), Google and Microsoft OAuth (mail, calendar), Stripe (billing), Recall.ai (meeting notetaker), DBOS (durable workflows), PostHog, Microsoft Clarity, New Relic, Pylon (support widget), Cloudflare Turnstile, HubSpot. [static]
- **Public API:** `public/v1` exposes agent CRUD and `outbound_call`, with API keys managed in the app. [static]

## Information architecture [UI]

Sidebar: **Overview**, **Assistants**, **Conversations**, and **More**. More contains Phone Numbers, WhatsApp, Scheduling, Email Agents, Contacts and Integrations.

| Screen | What it shows |
|---|---|
| Overview `/` | Calls answered, calls handled by assistants, time saved, call-history chart (calls/minutes), credit balance and spend per assistant. Insights unlock after 15 conversations. |
| Assistants `/agents` → `/agents/:id/{general,rules,knowledge,prompt,technical}` | Per-assistant config (below). Fixed bottom bar with **Assign Number** and **Test call** (a browser web call). |
| Conversations `/inbox` | One feed for all channels. The detail view has a title, channel (e.g. *Webcall*), duration, AI summary, timestamped transcript (Assistant/Caller), extracted **Results**, and **Export**. |
| Phone Numbers | Tabs All / Inbound / Outbound / WhatsApp. Buy a number. Table of Name / Number / Usage / Assignment. |
| WhatsApp | WhatsApp Business activation, which needs a subscription. |
| Scheduling | Event types (General, Availability, Notifications, Data Collection, Agents, Advanced). Connect a Google or Microsoft calendar. Public booking page. |
| Email Agents | One view per mailbox with tabs Inbox, Sent, Assistant, Context, Rules, Knowledge, Settings, and an **Activate Email Agent** action. |
| Contacts | Import and create contacts. "Intelligent fields" are AI-maintained per-contact attributes, e.g. *preferences*, *availability*. |
| Integrations | Catalogue by category (CRM, Calendar, Communication, PBX, E-commerce, Automation, vertical-specific). You enable an integration from inside an assistant. Examples: WhatsApp Business, Google Mail, Outlook Mail, Outlook Calendar, Calendly, HubSpot (early access). |

## Voice assistant model

### General tab [UI]
Name, voice, language, and start message with personalisation rules (e.g. greet a known contact by first name). Abilities you can toggle: Connect Contacts, Send Email, Transfer Calls, Send SMS, Send WhatsApp, Book Appointments, Web Search, Integrations, Webhooks.

### Rules tab [UI]
- **System rules** (always on): short answers, asks clarifying follow-ups, polite and calm, asks one question at a time and never asks for the same thing twice.
- **Configurable rules:** AI disclosure (default *only when asked*), communication style, off-topic and small-talk handling, angry caller (stay calm, offer escalation), sales calls and competitor comparisons (decline politely, stay neutral), pronunciation, and free-text custom rules.

### Knowledge and Prompt tabs [UI]
- Knowledge: sources, information entries, opening hours, knowledge base, company information. Supports RAG (`ragMode: selected_chunks`) [API].
- Custom Prompt: free text up to 100,000 characters, with an unsaved-changes counter.
- Static endpoints also describe **knowledge gaps**: questions the agent could not answer. An operator can resolve them (which creates an entry) or dismiss them. [static]

### Technical tab [UI]
Guardrails and content moderation (always on). Maximum call duration (minutes). Silence timeout (seconds). Audio recording, kept 30 days; the agent must announce it. Automatic deletion when the caller refuses recording (deletes the recording and transcript and skips post-call actions). Ambient sound (office or conference room) with a volume setting. Technical terms (STT key terms).

### Assistant object (from `GET /api/agents/:id`) [API]
The fields map directly to product capabilities:

| Area | Fields (example values) |
|---|---|
| Persona and LLM | `prompt`, `companyInfos` (markdown), `language` (40+ incl. `multi`), `temperature` (0.1), `formality`, `model` |
| Conversation rules | `conversationRules.{aiDisclosure, offTopic, angryCaller, salesCalls, customRules[]}` |
| Greeting | `beginMessage`, `beginMessageRules` (personalised from contact fields), `isBeginMessageInterruptionDisabled` |
| STT | `sttProcessor` (`soniox`), `keyTerms`, `highPrecisionKeywords[{keyword, example}]` |
| TTS | `voiceId`, `voiceType` (`inworld` / ElevenLabs), `voiceSpeed` (1.35), `elevenlabsVoiceResidency` (`europe`), `pronunciationDictionary` |
| Turn-taking | `interruptionSensitivity` (0.5), `isInterruptionDisabled`, `turnEagerness`, `backChannelChatMode` |
| Call limits | `endCallAfterSilenceTimeout` (10 s), `maxCallDuration` (20 min) |
| Recording | `isAudioRecordingEnabled`, `isRecordingOptOutEnabled` |
| Data capture | `isVariableExtractionActive`, `extractionPropertiesSchemaJson` (JSON Schema), `allowedContactFields`, `allowLandlineContactMatching` |
| Hooks | `isInboundHookActive`, `inboundHookUrl/Method/HeadersJson/BodyJson` (templated with `{{fromNumber}}`, `{{toNumber}}`) to fetch caller context before the call |
| Transfer and DTMF | `isFreeTransferEnabled`, `freeTransferDestinationType` (phone / SIP / user / PBX group), `isInternationalTransferAllowed`, `isDtmfCodeSendingEnabled` |
| Extensibility | `mcpServers[]`, `fileUploads[]`, `isGuardrailActive`, `phoneNumbers[]` |

### Observed call quality [UI]
In the test web call (2:09), the STT split the caller's turns at hesitations ("um", pauses). The caller started a sentence, got cut off, and the assistant asked for five fields in one turn even though the system rules say one question at a time. Endpointing and turn-taking quality matter more to the experience than any single feature.

## Email agent [static unless noted]
- Mailboxes connect through Google or Microsoft OAuth as **workspace connections**. You can't activate the agent until a mailbox is connected [UI]. Priced as a separate product: €49/month per inbox, beta [UI].
- `mailboxes/:workspaceConnectionId/threads|messages|attachments|drafts`: read threads, then create, edit and send drafts.
- `messages/:id/agent-runs` (+ `/test`): each processed email produces an inspectable **agent run**.
- `email-agent/settings`, `rules/configuration`, `rules/suggestions` (generate, refresh, delete): the system suggests rules from mailbox history. Also `knowledge/configuration`, `drafting/styles` (tone learned per user), `labels/refresh` (Gmail labels), `reconcile` (resync).
- Draft-first: the endpoints model a draft → review → send flow, not only sending automatically.

## WhatsApp [static unless noted]
- Onboarding through Meta **embedded signup** (`embedded-signup/complete`, connections, number statuses), with number verification by SMS or voice OTP (`number-verification/start`, `start-manual`, status).
- Message **templates** per WABA (create, send a test message): needed for messages sent outside the 24-hour customer-care window.
- Business profile read/update per number, plus enable/disable for WhatsApp Calling.
- Human-takeover endpoints include WhatsApp read receipts and typing indicators.
- Only available on a subscription [UI].

## Shared operations [static]
- **Unified inbox:** `inbox/feed` across types (calls, chats, emails). Per item: read, star, archive, tags. Feed export, permalinks, and a count of open human takeovers.
- **Human takeover** (chat channels): `takeover`, `release`, `end`, `messages`. A human pauses the AI, replies, then hands back.
- **Tasks:** post-conversation follow-ups with statuses OPEN / RESOLVED / DISMISSED and tags.
- **Contacts:** CRUD, bulk import (≤5,000), block, disable AI on WhatsApp per contact, organisations, configurable built-in and additional fields, creation gate, auto-populate.
- **Analytics:** `call-statistics` summary, timeline, costs, outcomes, intents, tags. Call recordings come with logs and an on-demand analysis.
- **Telephony:** Twilio purchase/import with regulatory/KYC bundles, BYO SIP trunks (digest, IP peering, registration), plus a full cloud PBX (ring groups, IVR menus, business hours, closed/missed-call handling, voicemail greetings via upload or TTS, hold music, caller allowlist, softphone). Also emergency settings and Twilio cost-limit alerts.
- **Other:** webchat agents with a hosted page, outbound campaigns with call intents, scheduling with booking webhooks, a meeting notetaker, an internal copilot ("personal assistant") that can draft and update assistants, and team chat.

## Pricing (observed) [UI]
- Voice: Solo €119/mo (1,000 min ≈ €0.12/min, 1 number), Team €359/mo (3,600 min), Scale custom (6,000+ min). Extra numbers €9/mo. Unlimited assistants.
- Pay-as-you-go credits: about €0.09/min on the test call.
- Email AI: €49/mo per inbox.

## Takeaways for our product
1. **One assistant, many channels.** Fonio has separate products (voice, chat, email) that share contacts, knowledge, inbox and integrations. We should start from one shared core and add channels as adapters.
2. **Opinionated defaults over prompt engineering.** The structured Rules tab and system rules make the product usable without prompt skills. Keep the raw prompt as an escape hatch.
3. **Structured extraction + summary per conversation** is the main output owners actually use (the "Results" panel).
4. **Inbound context hook** (look up the caller before the greeting) and **contact matching** enable personalisation cheaply.
5. **Draft-first email** and **human takeover** on chat channels are trust features. Ship them early.
6. **Knowledge gaps** turn failures into a backlog of content to add, a cheap feedback loop.
7. Voice quality (endpointing, interruptions, latency) is the main differentiator. Fonio exposes many tuning parameters, which suggests it struggles with this too.

## Limitations
- One test account on a pay-as-you-go plan. Subscription-only screens (WhatsApp, email agent) were seen only as gates. Their behaviour comes from static endpoint definitions alone.
- No outbound actions were triggered (no number purchase, emails or WhatsApp messages) to avoid cost and side effects.
- Static endpoint presence does not prove a feature is released. Feature flags and internal tools were not separated out.
