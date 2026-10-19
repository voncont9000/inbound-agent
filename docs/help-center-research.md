# Help center research: Fonio

Research for our in-app help center (`/help` in `web/`). Fonio's public help center was mapped on
2026-10-19. We reuse its **structure and topic coverage**, not its text or images: every article in
`web/src/help/articles/` is written from our own code and docs. Topics Fonio covers that we don't
support are listed under [Gaps](#gaps-fonio-topics-we-do-not-cover) instead of getting an article.

## Where it lives

- Help center: <https://fonio.info/en> (title "Fonio Help Center"). `docs.fonio.ai` redirects there
  (HTTP 301). `help.fonio.ai` has no site (Cloudflare error 1034).
- Linked from the app ("Open App" → <https://app.fonio.ai>), with `support@fonio.ai` as the email
  contact. The app also loads a Pylon support widget ([fonio-teardown.md](./fonio-teardown.md)).
- Languages: en, de, es, fr, it, nl, pl, pt-br (the language switcher sits in the header). The
  sitemap (<https://fonio.info/sitemap.xml>) lists about 1,000 URLs across all languages, about 125
  of them in English.
- It is a Next.js docs site in the style of Mintlify: a header search (⌘K), an "Ask assistant"
  AI chat (⌘I), tabs, a left sidebar, a right "On this page" table of contents, and a "Copy page"
  button.

## Information architecture

Four top tabs, each with its own sidebar.

| Tab | Sidebar (English titles, URL prefix `https://fonio.info/en/`) |
|---|---|
| **Get Started** | First Steps (`get-started/first-steps`), Phone Numbers (`get-started/connect-your-number`), How to write a great prompt (`get-started/write-a-great-prompt`), Knowledge Base (`get-started/knowledge-base`), Languages and multilingual calls (`get-started/languages`), Technical settings (`get-started/technical`), Contacts (`get-started/contacts`), Analytics (`get-started/analytics`), Troubleshooting (`get-started/troubleshooting`) plus ~20 troubleshooting sub-pages |
| **Product** | Actions: Send SMS (`actions/sms`), Using Web Search (`actions/websearch`), Send a WhatsApp message after a call (`actions/whatsapp-messages`), Transfer Call (`actions/call-transfer`), Set up the scheduler (`actions/appointments`), Send Email (`actions/email`). Products: Tasks (`products/tasks`), AI Chatbot (`products/webchat`), WhatsApp (`products/whatsapp`). Phone numbers: Import a phone number (`phone-numbers/import-number`), fonio PBX (`phone-numbers/pbx`) |
| **Integrations** | Developers: HTTP Request, Microsoft account and shared mailboxes, Test Context, Inbound Webhook, Outbound calls, Outbound API (`developers/*`). SIP Numbers (`phone-systems/sip-numbers` + 3CX, Yeastar, easybell, Telnyx, Twilio, Zadarma). About 30 vendor pages under `integrations/*` (Airtable, Google Sheets, HubSpot-style CRMs such as Pipedrive and GoHighLevel, Make, n8n, Zapier, Slack, Shopify, WooCommerce, Xero, …) |
| **Partner** | Reseller programme: partner hub and academy, onboarding, portal tracking, sales and marketing resources, customer lifecycle, legal (DPA, hosting), pricing and billing (`partner-program/*`, `sales-resources/*`, `legal/*`, `pricing-billing/*`, …) |

Troubleshooting sub-pages (`get-started/troubleshooting/*`): assistant-behavior, knowledge-base,
business-info, call-forwarding, phone-numbers, sip, prompt-and-tools, notifications-email, sms,
appointments, webhooks, call-history, scheduler, outbound-campaigns, subscription-billing,
api-keys, integrations, users-partners, report-a-problem.

The Troubleshooting hub opens with a "Where is the problem?" table that maps symptoms to the
matching category page, then groups those pages (assistant and knowledge; calls and phone
numbers; messages and appointments; integrations and API; account and administration), and
ends with "Before you contact us" and a copyable report template.

## Article template

Seen across first-steps, call-transfer, tasks, technical, contacts, analytics, email,
appointments, whatsapp and troubleshooting/phone-numbers:

1. **Breadcrumb** (tab/section), **H1 title**, and a one-sentence **summary** under it.
2. **Intro paragraph**: what the feature does and when to use it. Beta or plan callouts go here
   ("Beta", "Tip").
3. **Task sections** as H2s written as actions ("Add a destination", "Write a condition",
   "Choose availability"), often numbered ("1. Create your first assistant", "1.1 General"). Each
   one has a short paragraph and an annotated **screenshot** of the matching panel. Pages carry
   1–19 images (first-steps has 19; troubleshooting has 1).
4. **Good to know / common problems**: H2 or H3 Q&A blocks ("What happens if nobody
   answers?", "Anonymous numbers"). Troubleshooting pages use a fixed *Problem → Possible cause →
   Solution* pattern and end with a checklist.
5. **Good use cases** and **copyable blocks** (prompt snippets, report template).
6. **How to test**: almost every feature page ends with a "Test the …" section.
7. **Related articles**: three linked titles, sometimes across tabs.
8. Footer: **"Was this page helpful? Yes / No"**, **Previous / Next** links in sidebar order,
   and the right-hand **On this page** table of contents.

Search: the header search (⌘K) runs over all pages, and the "Ask assistant" AI answers from
the help center ("Answers come from the fonio help center").

## What we built (mapping)

| Fonio pattern | Ours |
|---|---|
| Tabs + sidebar | One left category nav (Getting started, Assistant settings, Channels, Conversations and contacts, Abilities, Account and privacy, Troubleshooting). Our product is smaller, so tabs aren't needed. |
| Header search | Client-side search over title, summary and body on `/help` |
| H1 + summary + steps + good to know + test + related | Same article template in Markdown with front matter (`title`, `category`, `order`, `summary`, optional `related`) |
| Screenshots | Not in articles yet. The UI is changing quickly while settings and the shell are reworked, so screenshots would go stale. |
| Was this page helpful? | Yes/No, stored in `localStorage` only |
| Previous / Next | Previous/next within the category |
| Contextual help | A small reusable `HelpLink` ("?") component, linked from settings tabs |
| Troubleshooting hub | A troubleshooting article with symptom → cause → fix sections, plus a "report a problem" checklist |
| Ask assistant (AI) | Not built (gap) |

## Gaps: Fonio topics we do not cover

These are Fonio help topics whose features our product doesn't have, so there is no article for
them. Each one is a candidate for the roadmap.

- **SMS** sending (`actions/sms`, troubleshooting/sms).
- **Web search** during calls (`actions/websearch`).
- **WhatsApp message after a call** (`actions/whatsapp-messages`), plus outbound WhatsApp and
  approved **message templates**. We create a task when a reply falls outside the 24-hour window.
- **Hosted scheduler**: a booking page, event-type notifications and reminders, Microsoft calendar
  (`actions/appointments`, troubleshooting/scheduler). We book into Google Calendar only, during
  calls and WhatsApp chats, without rescheduling or cancelling.
- **Web chat widget / AI chatbot** (`products/webchat`).
- **Buying or importing numbers in-app, SIP trunks, PBX** (`phone-numbers/*`,
  `phone-systems/sip-numbers/*`, troubleshooting/sip). Our numbers are Twilio numbers connected
  manually by their number.
- **Carrier call-forwarding codes** to divert an existing number (`get-started/connect-your-number`).
- **Outbound calls, campaigns and the outbound API** (`developers/outbound-*`).
- **Inbound webhook / test context** (looking up caller context before the greeting), the
  in-call **HTTP request** tool, API keys and the public API.
- **Microsoft / Outlook** mailboxes and shared mailboxes (`developers/microsoft`). We only support Gmail.
- **Email auto-send.** Our Gmail is draft-only by design.
- **Third-party integrations catalogue** (CRMs, Make/n8n/Zapier, Slack, e-commerce, vertical
  tools). We have Google Sheets rows and signed webhooks as after-conversation actions only.
- **Contacts import (CSV), blocking numbers, intelligent fields, greeting known callers by name**
  (`get-started/contacts`). Our contacts are built automatically from conversations; operators can
  merge or split them.
- **Analytics filters and exports** beyond the Home dashboard and inbox filters (`get-started/analytics`).
- **Ambient sounds, creativity/temperature, response pace** (`get-started/technical`).
- **Prompt builder assistant ("Nio")** and industry prompt templates beyond our template list.
- **Team invitations and user management UI** (troubleshooting/users-partners). Owner/staff roles
  exist, but members can't be invited from the app yet.
- **Billing, subscriptions, credits and invoices** (`pricing-billing/*`, troubleshooting/subscription-billing).
- **Partner / reseller programme** (the whole Partner tab).
- **Help center localisation** (8 languages) and the **"Ask assistant" AI** over help articles.
- **Video guides and screenshots** in articles.
