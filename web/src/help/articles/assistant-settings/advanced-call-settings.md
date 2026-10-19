---
title: Advanced call settings
category: assistant-settings
order: 5
summary: Review call behaviour, readback, live actions, booking timing and urgency rules.
related: [assistant-settings/call-recording, abilities/calendar-booking, abilities/call-forwarding]
---

Advanced settings control how the assistant handles a live call and when it asks for team help. They have template defaults, so change them only when you understand the effect.

## Review call behaviour

1. Open the assistant's settings and select **Advanced**.
2. Review the call limits and silence settings in **Call settings**.
3. In **Readback mode**, choose whether the assistant reads back nothing, email addresses, or fields marked critical.
4. Review the **Review threshold**. Fields extracted below that confidence are sent to the review queue.

## Review live actions and booking timing

1. Check which **Live actions** are enabled. Actions include confirming critical fields, flagging urgency, requesting a callback, booking or transferring a call.
2. If you use appointment booking, review **Booking timing**, including notice, horizon, buffers and offered slots.
3. Check that the relevant abilities and required channel connections are configured.

## Set urgency and forwarding messages

1. Under **Urgency rules**, choose a **Keyword** or **Date deadline** rule and set its details.
2. In call forwarding settings, review the messages used for a successful handoff or a failed transfer.
3. Choose **Save changes** and test a representative call.

### Good to know

Live actions add latency and default to none. Booking requires Google Calendar and the booking live actions; call transfer needs at least one configured transfer target.
