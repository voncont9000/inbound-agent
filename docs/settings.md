# Assistant settings layout

Settings live at `/assistants/:id/settings/:tab` (`general`, `rules`, `knowledge`, `abilities`,
`channels`, `advanced`). One **Save changes** button saves the whole `AssistantConfig` from any tab.
Validation errors are routed to the tab that owns the field by `tabForPath` in
`web/src/lib/settingsTabs.ts`; keep that function in sync when a control moves.

Each setting is shown exactly once. The settings reorganization is UI-only: it does not change the
config shape or existing saved assistants. The template library and new-assistant defaults are
documented in [templates.md](templates.md).

| Tab | Owns |
| --- | --- |
| General | Assistant name, business name, language and voice, greeting, **Instructions** (custom prompt), **Services and intake fields** |
| Rules | Rule presets, custom rules, pronunciation, sentence urgency rules, keyword and date urgency rules |
| Knowledge | Office hours, FAQs, knowledge sources and digest |
| Abilities | Calendar booking (appointment types, booking timing and holding messages), call forwarding (transfer targets, forwarding messages), after-conversation actions, readback (mode and live confirmation) |
| Channels | Read-only channel list and a link to workspace channel management; email triage |
| Advanced | Live actions that no ability card owns (`flag_urgent`, `request_callback`, `log_complaint`, `end_call`), review threshold, call settings and recording |

## Speech speed

At 1.00×, Speechify uses a natural conversational pace for every voice. Slower measured voices
are sped up toward 175 words per minute; faster voices keep their natural pace. The slider
adjusts relative to that baseline, so lower is slower and higher is faster.

## Voice picker

The General tab shows up to six recommended voices for the selected language: the current voice,
the catalogue default, then voices for the exact locale, alternating genders. A search box (name,
accent, locale) and gender/accent filters search the whole catalogue; **Show all N voices** expands
the list. Each voice keeps its Play/Stop sample button. The recommendation is computed in the
browser (`web/src/lib/voices.ts`); the `/voices` API is unchanged.

## Consolidation audit (2026-10-19)

| Setting | Old location(s) | New location | Reason |
| --- | --- | --- | --- |
| Config version history and diff | Settings page, below the tabs | Not shown | Product owner request. Backend versioning and `GET /assistants/{id}/versions` are unchanged; the save bar says "All changes saved" instead of a version number. |
| Voice list | General: every catalogue voice | General: 6 recommended + search, gender/accent filters, "Show all voices" | The full catalogue was too long to scan. |
| Instructions | General: "Instructions" textarea | General › Instructions: "Custom prompt" | Just the prompt; the structured details have their own card. |
| Services and intake fields | Abilities: "Services and intake fields" card with an on/off switch | General › "Services and intake fields" card, collapsed to a count summary; "Suggest from prompt" calls `POST /assistants/{id}/services-draft` (Gemini, `build_services_draft_prompt`/`parse_services_draft`) and replaces the services in the unsaved draft; "Edit services and intake fields" opens the editor | They only shape what the assistant asks for, so they belong with the prompt. Kept structured because extraction, critical-field readback and date urgency rules use the generated field keys. The on/off switch was dropped (an empty list means none). |
| Price list | Knowledge: "Price list" | No editor (config field kept) | Only rendered into the prompt ("PUBLISHED PRICES"). Prices belong in Knowledge (FAQs, digest) or the custom prompt; `price_list` is still accepted by the API and rendered when set. |
| Contact details | Knowledge: "Contact details" | No editor (config field kept) | Only rendered into the prompt ("CONTACTS"). Put contact details in Knowledge or the custom prompt; `contacts` is still accepted by the API and rendered when set. |
| Readback mode | Abilities: Readback on/off card (said "choose under Advanced"); Advanced: "Readback mode" radios (off/email/critical) | Abilities › Readback: switch = off, radios = email / critical | The same setting was in two places. |
| `confirm_critical_fields` live action | Advanced: Live actions | Abilities › Readback: "Record the caller's confirmation" | It is the live half of readback; turning readback off also removes it. |
| `check_availability`, `book_appointment` live actions | Abilities: Calendar booking switch; Advanced: Live actions checkboxes | Abilities › Calendar booking only (switch, plus a "Turn them on" button if they are missing) | Duplicate control; the booking card note used to send users to Advanced. |
| `transfer_call` live action | Abilities: Call forwarding switch; Advanced: Live actions checkbox | Abilities › Call forwarding switch only | Duplicate control. |
| Other live actions (`flag_urgent`, `request_callback`, `log_complaint`, `end_call`) | Advanced: Live actions | Advanced: Live actions (unchanged) | No ability card owns them. |
| Booking timing and holding messages (calendar ID, holding lines, notice, horizon, buffer, slot step, max slots, attendee updates) | Advanced: "Booking timing" (empty until booking was on under Abilities) | Abilities › Calendar booking › "Booking timing and messages" (collapsed) | All booking settings in one card. |
| Appointment types | Abilities › Calendar booking | Unchanged | Structured; used by the booking tools. |
| Transfer targets | Abilities › Call forwarding | Unchanged | Structured; used by `transfer_call`. |
| Forwarding handoff / no-answer messages | Advanced: "Call forwarding messages" | Abilities › Call forwarding › "Forwarding messages" (collapsed) | All forwarding settings in one card. |
| Keyword and date urgency rules | Advanced: "Urgency rules" | Rules: one "Urgency" section; structured rules are under "More rule types: keywords and date deadlines" | All `urgency_rules` in one section. Sentence rules appear first; date rules still reference intake field keys. |
| Sentence urgency rules | Rules: "Urgent if…" | Rules: "Urgency" | Sentence rules are the primary option; keyword and date rules are progressively disclosed. |
| Channel connections | Workspace **Channels** page | Assistant settings → Channels | The settings tab shows this assistant's connections and links to workspace management for add, reassign, remove, and Gmail filters. |
| Office hours, FAQs, knowledge sources | Knowledge | Unchanged | Facts the assistant looks up, not prompt instructions. |
| Rule presets, custom rules, pronunciation | Rules | Unchanged | — |
| Email triage | Channels | Unchanged | — |
| Review threshold, call settings, recording | Advanced | Unchanged | Technical values with template defaults. |
