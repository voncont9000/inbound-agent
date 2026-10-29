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
3. Choose an action card. Add more actions with **Add another action**; each action has its own settings.
4. Configure its recipient or destination, trigger and message or field values.
5. In a subject, message or Sheets column value, choose **Insert variable** to select a conversation or intake field. Choose **Custom variable…** to add a named placeholder.
6. For a Sheets action, paste the link from your browser into **Google Sheet link**. You can also enter a spreadsheet ID. A new Sheets action starts with the columns Started, Name, Channel and Summary.
7. Assistant settings save automatically as you edit.

## Preview an action

1. Choose **Send test** beside the action you want to check.
2. Review the previewed message, spreadsheet row or webhook payload.
3. Correct the configuration and run the preview again if needed.

### Good to know

**Send test** previews the action; it does not send the email, write the spreadsheet row or call the webhook. Browser test calls do run after-conversation actions: email subjects start with **[Test]**, and webhook payloads include `"test": true`. Delivery is at-least-once, so a retry can deliver an action more than once.
