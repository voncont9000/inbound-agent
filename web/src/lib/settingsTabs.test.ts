import { describe, expect, it } from "vitest";
import { isSettingsTab, settingsTabPath, tabForPath } from "./settingsTabs";

describe("settings tabs", () => {
  it("builds URL-addressable tab paths", () => {
    expect(settingsTabPath("abc")).toBe("/assistants/abc/settings/general");
    expect(settingsTabPath("abc", "rules")).toBe("/assistants/abc/settings/rules");
    expect(settingsTabPath("abc", "abilities")).toBe("/assistants/abc/settings/abilities");
    expect(isSettingsTab("advanced")).toBe(true);
    expect(isSettingsTab("unknown")).toBe(false);
    expect(isSettingsTab(undefined)).toBe(false);
  });

  it("maps error paths to the tab that shows the field", () => {
    expect(tabForPath("business_name")).toBe("general");
    expect(tabForPath("rules.style")).toBe("rules");
    expect(tabForPath("urgency_rules.0.text")).toBe("rules");
    expect(tabForPath("language")).toBe("general");
    expect(tabForPath("voice_id")).toBe("general");
    expect(tabForPath("policies.0")).toBe("rules");
    expect(tabForPath("office_hours.closes")).toBe("knowledge");
    expect(tabForPath("contacts.office.key")).toBe("general");
    expect(tabForPath("services.boiler_repair.fields.0.label")).toBe("general");
    expect(tabForPath("booking.appointment_types.0.label")).toBe("abilities");
    expect(tabForPath("booking.horizon_days")).toBe("abilities");
    expect(tabForPath("call_settings.max_call_minutes")).toBe("advanced");
    expect(tabForPath("recording.enabled")).toBe("advanced");
    expect(tabForPath("transfer_targets.0.phone")).toBe("abilities");
    expect(tabForPath("urgency_rules.1.field")).toBe("rules");
    expect(tabForPath("urgency_rules.0.text")).toBe("rules");
    expect(tabForPath("price_list.0.price")).toBe("general");
    expect(tabForPath("readback")).toBe("abilities");
    expect(tabForPath("transfer_handoff_message")).toBe("abilities");
    expect(tabForPath("live_actions")).toBe("advanced");
    expect(tabForPath("email_triage.enquiry")).toBe("channels");
    expect(tabForPath("_form")).toBe("general");
  });
});
