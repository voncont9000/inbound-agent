---
title: Connect Gmail
category: channels
order: 2
summary: Connect Gmail for draft-only email triage and review the assistant's email labels.
related: [conversations/inbox-and-conversations, abilities/calendar-booking, troubleshooting/troubleshooting]
---

Gmail lets the assistant triage incoming messages and prepare replies as drafts. It never sends messages and does not attach files to drafts.

## Connect Gmail

1. Open the assistant's settings and select **Channels**.
2. Choose **Add a channel**, then select **Gmail inbox**.
3. Choose **Connect with Google** and follow the connection prompts. In a deployment using the fake Gmail provider, enter the mailbox address instead.
4. Review the Gmail triage categories and adjust them if needed.

## Choose which emails reach the assistant

1. In the Gmail channel row, open **Email filters**.
2. Add a filter, choose **Sent to (To)**, and enter a value such as `@yourdomain`.
3. Choose **Save filters**. If you add multiple filters, an email is included when it matches at least one of them.

Recipient filters check the `To`, `Cc`, `Delivered-To`, `X-Original-To`, `X-Forwarded-To` and `Envelope-To` headers. Messages that do not match remain in Gmail and are labelled **AI/Skipped**.

## Review triaged messages

1. Check the Gmail inbox and the assistant's triage result.
2. Review the category: `enquiry`, `needs_human`, `spam` or `not_relevant`.
3. Find messages needing a person under the Gmail label **AI/Needs human**.
4. Find skipped messages under **AI/Skipped**.
5. Review any draft in Gmail and send it yourself only after checking it.

### Good to know

Message attachments may be read as context, but they are not added to a reply draft. Gmail does not create Calendar bookings; use the supported live call booking flow instead.
