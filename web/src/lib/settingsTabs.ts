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

/** The tab that shows the input for a config error path such as `services.x.fields.0.name`. */
export function tabForPath(path: string): SettingsTabId {
  const root = path.split(".")[0];
  switch (root) {
    case "rules":
      return "rules";
    case "language":
    case "voice_id":
    case "instructions":
    case "services":
    case "price_list":
    case "contacts":
      return "general";
    case "policies":
    case "urgency_rules":
      return "rules";
    case "office_hours":
    case "knowledge":
      return "knowledge";
    case "booking":
    case "transfer_targets":
    case "transfer_handoff_message":
    case "transfer_failed_message":
    case "readback":
    case "post_actions":
      return "abilities";
    case "email_triage":
      return "channels";
    case "live_actions":
    case "call_settings":
    case "recording":
    case "review_confidence_threshold":
      return "advanced";
    default:
      return "general";
  }
}
