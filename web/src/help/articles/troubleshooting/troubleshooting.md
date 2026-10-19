---
title: Troubleshoot common issues
category: troubleshooting
order: 1
summary: Use the checks below to resolve common setup and conversation problems.
related: [troubleshooting/report-a-problem, assistant-settings/knowledge, channels/gmail]
---

Start with the relevant check below. If the issue remains, collect the conversation details and [report a problem](/help/troubleshooting/report-a-problem).

| Symptom | First check |
| --- | --- |
| The assistant gives an incorrect answer | Check the relevant Knowledge entry and rules, then test the updated answer. |
| A website import says it returned no readable text | Check the website address and try adding a document or entering the information directly. |
| Gmail says it is not enabled for this deployment | Ask your administrator whether Gmail is available for your workspace. |
| Calendar is not enabled or booking cannot connect | Ask your administrator whether Google Calendar is available, then check the connection in Channels. |
| The booking card says live actions are missing | Enable the availability and booking options under Advanced → Live actions. |
| A WhatsApp reply did not send after 24 hours | A template is required outside the customer service window; review the template-needed task. |
| Call forwarding has no targets | Add at least one transfer target under Abilities and check that transfer is enabled under Advanced. |

## The assistant gives the wrong information

1. Open the conversation and note the question and the assistant's answer.
2. Check the assistant's Knowledge settings for the current answer.
3. Correct the knowledge or rule, choose **Save changes** or **Save digest** as appropriate, and test again.

## A channel is unavailable

1. Check the message shown on the Channels tab.
2. Ask your administrator whether Gmail or Google Calendar is enabled for your workspace.
3. For call routing or phone service issues, ask your administrator to check the connection.

### Good to know

Gmail replies are drafts for a person to review, not automatically sent messages. WhatsApp templates are not managed in the app.
