---
title: Configure after-conversation actions
category: abilities
order: 3
summary: Choose what to notify or record when a conversation ends.
related: [getting-started/test-call, assistant-settings/settings-overview, conversations/inbox-and-conversations]
---

After-conversation actions can notify staff, email a customer, add a Google Sheets row or send a webhook when a conversation ends. Configure only the actions your team needs.

## Add an action

1. Open the assistant's settings and select **Abilities**.
2. Enable **After-conversation actions**.
3. Add the action type and configure its recipient or destination, trigger and message or field values.
4. Use the available conversation and contact values in templates where needed.
5. Choose **Save changes**.

## Preview an action

1. Choose **Send test** beside the action you want to check.
2. Review the previewed message, spreadsheet row or webhook payload.
3. Correct the configuration and run the preview again if needed.

### Good to know

**Send test** previews the action; it does not send the email, write the spreadsheet row or call the webhook. Delivery is at-least-once, so a retry can deliver an action more than once. Test calls never run after-conversation actions.
