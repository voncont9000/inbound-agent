import { describe, expect, it } from "vitest";
import { isSettingsTab, settingsTabPath, tabForPath } from "./settingsTabs";

describe("settings tabs", () => {
  it("builds URL-addressable tab paths", () => {
    expect(settingsTabPath("abc")).toBe("/assistants/abc/settings/general");
    expect(settingsTabPath("abc", "abilities")).toBe("/assistants/abc/settings/abilities");
    expect(isSettingsTab("advanced")).toBe(true);
    expect(isSettingsTab("unknown")).toBe(false);
    expect(isSettingsTab(undefined)).toBe(false);
  });

  it("maps error paths to the tab that shows the field", () => {
    expect(tabForPath("business_name")).toBe("general");
    expect(tabForPath("language")).toBe("general");
    expect(tabForPath("voice_id")).toBe("general");
    expect(tabForPath("policies.0")).toBe("rules");
    expect(tabForPath("office_hours.closes")).toBe("knowledge");
    expect(tabForPath("contacts.office.key")).toBe("knowledge");
    expect(tabForPath("services.boiler_repair.fields.0.label")).toBe("abilities");
    expect(tabForPath("booking.appointment_types.0.label")).toBe("abilities");
    expect(tabForPath("booking.horizon_days")).toBe("advanced");
    expect(tabForPath("transfer_targets.0.phone")).toBe("abilities");
    expect(tabForPath("urgency_rules.1.field")).toBe("advanced");
    expect(tabForPath("email_triage.enquiry")).toBe("channels");
    expect(tabForPath("_form")).toBe("general");
  });
});
