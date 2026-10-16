# PRD: Inbound Agent

| | |
|---|---|
| Status | Draft v0.3 |
| Owner | Vincent (voncont9000) |
| Reference analysis | [Fonio teardown](./fonio-teardown.md), [Context graph](./context-graph.md) |

## 1. Summary
A multi-tenant SaaS for UK businesses, starting with **consumer-facing (B2C) law firms**: an AI agent that answers inbound customer enquiries on **phone** (speech-to-text → LLM → text-to-speech), **email** (Gmail) and **WhatsApp**, for one business. All channels share one knowledge base, one contact store and one conversation inbox. It answers questions, captures structured lead and request data, books or routes requests, and hands off to a human when needed. The business owner sees every conversation with a summary and the extracted results.

## 2. Problem
Small and mid-sized businesses (cafés, trades, clinics, agencies, wholesalers) miss calls, answer emails late and handle WhatsApp by hand. The enquiries are repetitive: hours, prices, availability, orders, bookings, callbacks. Existing tools such as Fonio work, but they split voice, email and WhatsApp into separately priced products and depend on a lot of tuning parameters.

**Launch vertical: UK B2C law firms.** High-street firms (family, conveyancing, wills and probate, immigration, employment, personal injury) get many new-client enquiries by phone and email. Most are intake: what happened, how urgent, who the other side is, what it costs, and when they can speak to a solicitor. Every missed call is a lost instruction. Existing clients also call for matter updates, which takes fee-earner time. All of this happens under SRA regulation, strict confidentiality and special-category data.

## 3. Goals and non-goals
**Goals (v1)**
- G1: Answer 100% of inbound calls 24/7, with the response grounded in business knowledge.
- G2: Draft (and optionally send) replies to inbound Gmail email.
- G3: Answer WhatsApp messages inside the 24-hour customer-care window.
- G4: For every conversation, store a transcript, summary, intent, outcome and structured extracted fields, and link it to a contact.
- G5: Let a human take over or approve on every channel.

**Non-goals (v1)**
- Payment follow-up and other proactive outbound workflows (the data model supports them, see §7.10), outbound campaigns, cloud PBX/IVR, SMS, webchat, Microsoft 365 mail, meeting notetaker, marketplace of integrations, multi-tenant reseller features.

## 4. Users
- **Owner/admin:** sets up the assistant, connects channels, reviews conversations, edits knowledge.
- **Staff/agent:** gets handoffs and tasks, takes over chats, approves email drafts. In law firms: receptionists, paralegals, fee earners, and the complaints/compliance officer (COLP).
- **End customer:** calls, emails or messages the business.

## 5. Success metrics
| Metric | v1 target |
|---|---|
| Calls answered | ≥ 99% |
| Voice response latency (end of caller speech → first audio) | p50 ≤ 800 ms, p95 ≤ 1.5 s |
| Conversations resolved without human (per owner rating) | ≥ 60% |
| Email drafts sent with no or minor edits | ≥ 70% |
| Extraction field accuracy (spot-checked) | ≥ 90% |
| Knowledge gaps resolved within 7 days | ≥ 50% |

## 6. Core concepts
- **Workspace (tenant):** one business. Has members with roles (owner, staff). Data is strictly isolated per tenant.
- **Assistant:** persona, language(s), voice, rules, prompt, knowledge, abilities and an extraction schema. One assistant serves all channels, with per-channel overrides (greeting, length, tone).
- **Channel connection:** phone number, Gmail mailbox (OAuth) or WhatsApp number (WABA).
- **Contact:** matched by phone/email/WhatsApp ID. Has built-in and custom fields, some AI-maintained ("intelligent fields").
- **Conversation:** channel, participants, messages/turns, transcript, recording (voice), summary, intent, outcome, tags, extracted results, status (open / handled / needs human / closed).
- **Knowledge:** company info, FAQ entries, documents/URLs (chunked for RAG), opening hours. **Knowledge gaps** are generated from questions the assistant could not answer.
- **Task:** a follow-up created from a conversation (callback, quote, order). Statuses: open / resolved / dismissed.

## 7. Functional requirements

### 7.1 Assistant configuration
- FR-A1: Create an assistant from a guided setup: business name, website URL (scraped into company info), opening hours, top FAQs, language, voice.
- FR-A2: Structured **rules** with good defaults, plus custom free-text rules. The defaults: AI disclosure (when asked / always), tone/formality, off-topic handling, angry customer (de-escalate, offer human), sales/competitor calls (decline politely), pronunciation dictionary.
- FR-A3: Fixed system behaviours: concise, one question at a time, never ask for the same data twice, confirm critical data (phone, email, dates) by reading it back.
- FR-A4: Advanced **custom prompt** (≤ 100k chars) appended to the generated system prompt.
- FR-A5: **Extraction schema** (JSON Schema editor with presets such as lead, booking request, order, complaint), filled per conversation.
- FR-A6: Abilities you can toggle in v1: look up/create contact, create task, book appointment (Google Calendar), write/read Google Sheets, send email. Later: transfer/forward call, send WhatsApp from a call, web search, webhooks/custom tools.
- FR-A7: Test console: browser web call (voice) and simulated email/WhatsApp threads. Test conversations are marked as such.
- FR-A8: Versioned config with an unsaved-changes indicator and rollback.

### 7.2 Phone (STT/TTS)
- FR-P1: Buy or port a UK number (geographic or 03, Ofcom-compliant) through a telephony provider (Twilio first), or forward an existing number. Assign it to an assistant.
- FR-P2: Real-time pipeline: provider media stream → streaming STT → LLM with tool calls → streaming TTS → caller. Implement it behind provider-agnostic interfaces so STT/LLM/TTS vendors can be swapped.
- FR-P3: Turn-taking: voice-activity and semantic endpointing (don't cut the caller off at "um"), barge-in/interruption with adjustable sensitivity, optional backchannels, no-interrupt on the greeting.
- FR-P4: Personalised greeting. An optional **pre-call context hook** (HTTP request templated with `{{from}}`, `{{to}}`) runs before the greeting, then contact matching runs on caller ID.
- FR-P5: STT vocabulary boosting (key terms, names, products). TTS pronunciation dictionary. Voice speed.
- FR-P6: Call controls: max duration, silence timeout with re-prompt then hang-up, take-a-message fallback that creates a callback task. Later: warm or cold transfer/forwarding to a phone number with a whisper summary, DTMF sending.
- FR-P7: Recording off by default. When on, it is announced in the greeting. If the caller opts out, the recording is not stored and the transcript is kept or deleted per setting. Retention is configurable (default 30 days).
- FR-P8: Languages: English (UK accents and voices) at launch. More languages later via automatic language detection.

### 7.3 Email (Gmail)
- FR-E1: Connect a Gmail / Google Workspace mailbox via OAuth. Use the narrowest scopes possible (read, compose, send, labels).
- FR-E2: Ingest new mail with Gmail push notifications (Pub/Sub watch) and a periodic reconcile. Process only threads that match the inbox rules (labels, senders, exclusions such as newsletters, no-reply addresses, internal mail).
- FR-E3: Per-message **agent run**: classify (enquiry / spam / not relevant / needs human), retrieve knowledge and contact history, draft a reply in the thread, extract fields, apply labels. Each run can be inspected (inputs, tools, output).
- FR-E4: v1 is **draft only**: the draft is created in Gmail and in-app, and a human sends it. Or **escalate** (label plus notify staff). **Auto-send** per category is a later opt-in once draft acceptance rates show it can be trusted (see metrics).
- FR-E5: Learn drafting style (greeting, sign-off, length) from the owner's sent mail. The owner can edit it.
- FR-E6: Attachments: read text-based attachments for context. Never auto-send attachments in v1.
- FR-E7: Suggested rules generated from mailbox history, which the owner accepts or dismisses.

### 7.4 WhatsApp
- FR-W1: Connect a WhatsApp Business number via Meta embedded signup (Cloud API). Support number verification and business profile editing.
- FR-W2: Reply automatically to inbound messages within the 24-hour window. Typing indicator and read receipts.
- FR-W3: Messages outside the window use approved **templates**. Template management and test send.
- FR-W4: Media: receive images and documents (stored, passed to the LLM where supported). Transcribe voice notes with STT and answer them in text.
- FR-W5: Per-contact "AI off" switch. Staff takeover pauses the AI until released or a timeout passes.

### 7.5 Unified inbox and human handoff
- FR-I1: One feed across channels with filters (channel, status, tag, assistant, needs-human), search, star, archive, tags, export (CSV).
- FR-I2: Conversation detail: summary, intent, outcome, extracted results, timeline/transcript, recording player, linked contact, tasks.
- FR-I3: **Human takeover** on WhatsApp and email: take over, reply as staff, release back to AI, end. On phone: create an urgent callback task (call transfer comes later).
- FR-I4: Notifications for needs-human, new task and failed run (email plus a WhatsApp/Slack webhook).

### 7.6 Contacts
- FR-C1: Auto-match or create contacts across channels (phone in E.164, email, WhatsApp ID). Merge duplicates.
- FR-C2: CSV import (≤ 5,000 rows), custom fields, AI-maintained fields with a history of changes, block list.
- FR-C3: The assistant can read only fields the owner allows (allowed contact fields).

### 7.7 Knowledge
- FR-K1: Sources: company info (markdown), FAQ entries, website crawl, uploaded PDFs/DOCX, opening hours with holidays.
- FR-K2: RAG retrieval with citations stored in the run log. Answers outside the knowledge are declined or escalated (no hallucinated prices or availability).
- FR-K3: **Knowledge gaps:** unanswered questions are clustered and listed. Resolving one creates a knowledge entry.

### 7.8 Integrations and tools
**v1**
- FR-T1: **Calendar** (Google Calendar first, Outlook later): availability lookup for configured fee earners or consultation slots, create/reschedule/cancel event, confirmation by email. Booking rules per tenant (consultation types, durations, buffers, paid or free initial consultation).
- FR-T2: **Google Sheets**: (a) append one row per conversation with the extraction fields (intake log) to a sheet the firm picks; (b) read lookup tables (e.g. fee list, fee earners per practice area, office hours) as knowledge. OAuth, least-privilege scopes (`drive.file` where possible).
- FR-T3: **Email** actions: send a confirmation or summary email to the caller (from the connected Gmail mailbox, using templates), and notify staff by email.

**Later**
- FR-T4: Call transfer/forwarding (see FR-P6), sending WhatsApp messages from a call, web search, generic webhooks (before/during/after a conversation, templated request and response mapping), MCP tools.
- FR-T5: Legal practice management systems (e.g. Clio, LEAP, Actionstep), Outlook mail and calendar, Xero (payment follow-up).

### 7.9 Analytics and billing
- FR-M1: Dashboard: conversations per channel, answered calls, minutes, resolution rate, time saved, top intents, outcomes, new enquiries per practice area, knowledge gaps.
- FR-M2: Cost metering per conversation (telephony, STT, LLM, TTS, WhatsApp fees). Workspace spend limits and alerts.
- FR-M3: **Pricing is per answered call.** A call is billable when the AI answers it, the caller speaks, and the call lasts ≥ 15 s (threshold configurable by us). Not billable: test calls, calls flagged as spam or robocalls, calls dropped before the greeting ends. Each call stores a billable flag with the reason, shown to the tenant.
- FR-M4: Plans with a monthly number of included answered calls plus a per-call price above that (Stripe metered billing). Max call duration caps the cost per call. The dashboard shows the count of billable calls so far in the billing period.

### 7.10 Customer context graph
See [context-graph.md](./context-graph.md) for research and design.
- FR-G1: Every conversation on every channel links to a resolved **person** (and **organisation**) through deterministic identity matching (E.164 phone, email, WhatsApp ID). Fuzzy matches are suggestions for owner review, never auto-merged.
- FR-G2: Extraction after each conversation writes **facts** (bi-temporal, with source message and confidence), **commitments** (e.g. promise to pay, callback) and case links into the graph. Contradicting facts supersede older ones instead of overwriting them.
- FR-G3: At conversation start the agent receives a **context card** for the customer: identity, organisation, recent cross-channel interactions, open commitments and tasks, key facts. For voice it is loaded while the call is ringing.
- FR-G4: Personal or account details are disclosed only above an identity-verification threshold. Caller ID alone only allows personalisation.
- FR-G5: Owners can view a customer's timeline and graph, correct or reject facts, and merge or split contacts (audited and reversible).
- FR-G6: External business records (invoices, payments, orders, bookings) can be synced into the graph (Xero first) to support future payment follow-up.

### 7.11 Law-firm requirements (launch vertical)
- FR-L1: **No legal advice.** The agent gives general information only from the firm's approved knowledge base, says it is not giving legal advice, and routes advice questions to a solicitor (callback or booking). Configurable disclaimer.
- FR-L2: **Intake by practice area.** Preset extraction schemas (family, conveyancing, wills/probate, immigration, employment, personal injury, criminal) capture the matter type, short facts, key dates and **deadlines** (e.g. hearing date, tribunal time limits), funding (private, legal aid, no win no fee) and contact preferences.
- FR-L3: **Conflict-check data.** Intake captures names of other parties (opposing party, spouse, employer, other driver). These are flagged as "conflict check pending" on the enquiry. The agent never confirms the firm will act for someone.
- FR-L4: **Urgency and safeguarding.** Detect urgent or vulnerable situations (domestic abuse, a hearing or arrest today, deadlines within days, distress). The agent escalates at once (urgent task plus staff notification) and gives emergency guidance (999) where there is danger.
- FR-L5: **Confidentiality.** The agent never confirms or denies that someone is a client, and never discusses a matter until the caller is verified (FR-G4). It does not leave detailed voicemails or messages. Third parties (family members, the other side) get no information.
- FR-L6: **Price questions** are answered from the firm's published price and service information (required under the SRA Transparency Rules for areas such as residential conveyancing, uncontested probate, immigration, employment tribunals and summary motoring offences). The agent never invents a quote.
- FR-L7: **Complaints.** The agent recognises complaints, logs them as a complaint task for the firm's complaints handler, and gives the firm's complaints procedure and Legal Ombudsman information as published by the firm.
- FR-L8: **Existing clients** asking for updates: after verification, the agent takes a message for the fee earner (v1). Live matter status needs a practice management integration (later).
- FR-L9: **Supervision and audit.** The firm stays responsible for the service under SRA rules. Every conversation keeps its transcript, the knowledge sources used and the actions taken. The firm can review and correct them. The SRA's [AI warning notice](https://www.sra.org.uk/solicitors/guidance/misuse-ai/) focuses on inaccurate information and client confidentiality, which drive FR-L1, FR-L5 and the vendor DPAs.
- Out of scope for v1: AML/ID verification for client onboarding, engagement letters, taking payments.

## 8. Non-functional requirements
- **Latency:** see metrics. Stream everything. Region-pinned processing (EU first).
- **Reliability:** 99.9% call answer availability. If the AI pipeline fails, fall back to voicemail or transfer. Durable, idempotent processing for email and WhatsApp webhooks (retries, dedupe by message ID).
- **Security:** OAuth tokens and secrets encrypted at rest (KMS). Least-privilege scopes. RBAC (owner, staff). Audit log of config changes and human actions. Signed webhooks.
- **Multi-tenancy:** `tenant_id` on every row with Postgres row-level security. Tenant scope comes from auth, never from model output. Cross-tenant isolation tests in CI.
- **Special-category and criminal-offence data** (UK GDPR Art. 9/10) is common in legal intake: field-level encryption for intake facts, role-based access within a tenant, and no use of LLM providers that train on customer data.
- **Privacy/compliance:** UK GDPR / DPA 2018 (UK hosting, processor DPA, erasure and export per contact cascading through the context graph, retention settings). Ofcom rules for numbers. ICO registration. Recording consent. AI disclosure. WhatsApp Business policy and Google API user-data policy (needed for Gmail restricted-scope verification).
- **Safety:** content moderation guardrails. Prompt-injection defence for email and WhatsApp content (treat inbound text as untrusted; tool calls need policy checks). No auto-send of payments, legal or medical commitments.
- **Observability:** per-turn traces (STT, LLM, TTS timings, tool calls), run logs, cost per conversation.

## 9. Proposed architecture (v1)
- **Backend:** TypeScript (Node) API plus workers. Postgres with pgvector for data, RAG and the customer context graph (row-level security per tenant). Redis for realtime session state. A durable job queue for email and WhatsApp runs.
- **Voice gateway:** a separate realtime service handling telephony media streams (Twilio Media Streams / SIP). Built in-house, not on Vapi/Retell. Pluggable STT (e.g. Deepgram/Soniox), LLM (OpenAI/Anthropic), TTS (ElevenLabs/Cartesia). Open-source orchestration libraries (e.g. Pipecat, LiveKit Agents) are allowed inside our own service.
- **Channel adapters:** Gmail (Pub/Sub watch plus History API), WhatsApp Cloud API webhooks, telephony. All channels normalise into one `Conversation`/`Message` model and one agent runtime with shared tools.
- **Frontend:** React web app (assistant setup, inbox, contacts, knowledge, analytics).

## 10. Release plan
| Milestone | Scope |
|---|---|
| M0 Spike (1–2 wks) | Voice pipeline prototype on one UK Twilio number with latency measurement. Vendor choice. Context graph spike (Postgres vs Graphiti extraction). |
| M1 Voice MVP | Multi-tenant workspaces, assistant config (rules, knowledge, law-firm intake presets), inbound calls, take-a-message and urgent escalation, Google Calendar booking, Google Sheets intake log, email confirmation, recording, inbox with summary and results, contacts with context graph (identity, facts, context card). |
| M2 Gmail | OAuth connect, rules, draft-first agent runs, style learning, takeover. |
| M3 WhatsApp | Embedded signup, 24h replies, templates, voice notes, takeover. |
| M4 Ops | Knowledge gaps, analytics and per-answered-call billing, notifications. |
| M5 Actions | Call transfer/forwarding, WhatsApp sends from calls, web search, webhooks, practice management integration. |

## 11. Decisions
| Question | Decision (2026-10-16) |
|---|---|
| Launch market | UK |
| Tenancy | Multi-tenant SaaS from day one |
| Gmail sending | Drafts first, human sends. Auto-send later, opt-in |
| Voice layer | Build our own pipeline (no Vapi/Retell) |
| Cross-channel memory | Postgres-native customer context graph ([context-graph.md](./context-graph.md)) |
| v1 integrations | Calendar, Google Sheets, email. Later: call forwarding, WhatsApp sends, web search, webhooks |
| Launch vertical | UK B2C law firms |
| Pricing | Per answered call |

## 12. Open questions
1. How are email and WhatsApp conversations priced: included, or charged per conversation?
2. Which practice areas come first for the intake presets (e.g. family and conveyancing)?
3. Answered-call threshold (15 s proposed) and spam handling: are they acceptable commercially?
4. Which practice management system matters most to the target firms (Clio, LEAP, Actionstep)?
