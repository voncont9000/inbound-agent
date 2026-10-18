export const settingsTabs = [
  { id: "general", label: "General" },
  { id: "rules", label: "Rules" },
  { id: "knowledge", label: "Knowledge" },
  { id: "abilities", label: "Abilities" },
  { id: "channels", label: "Channels" },
  { id: "advanced", label: "Advanced" },
] as const;

export type SettingsTabId = (typeof settingsTabs)[number]["id"];

export function isSettingsTab(value: string | undefined): value is SettingsTabId {
  return settingsTabs.some(({ id }) => id === value);
}

export function settingsTabPath(assistantId: string, tab: SettingsTabId = "general"): string {
  return `/assistants/${assistantId}/settings/${tab}`;
}

const advancedBooking = /^booking\.(check_message|book_message|min_notice_hours|horizon_days|buffer_minutes|slot_step_minutes|max_slots_offered|send_updates)$/;

/** The tab that shows the input for a config error path such as `services.x.fields.0.name`. */
export function tabForPath(path: string): SettingsTabId {
  const root = path.split(".")[0];
  if (advancedBooking.test(path)) return "advanced";
  if (/^urgency_rules\.\d+\.text$/.test(path)) return "rules";
  switch (root) {
    case "rules":
      return "rules";
    case "policies":
      return "rules";
    case "office_hours":
    case "price_list":
    case "knowledge":
    case "contacts":
      return "knowledge";
    case "services":
    case "booking":
    case "transfer_targets":
    case "post_actions":
      return "abilities";
    case "email_triage":
      return "channels";
    case "readback":
    case "live_actions":
    case "urgency_rules":
    case "review_confidence_threshold":
    case "transfer_handoff_message":
    case "transfer_failed_message":
      return "advanced";
    default:
      return "general";
  }
}
