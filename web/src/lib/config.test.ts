import { describe, expect, it } from "vitest";
import { tenantFixtures } from "../mocks/fixtures";
import { validateAssistantConfig } from "./config";

describe("validateAssistantConfig", () => {
  it("accepts the configured demo assistant", () => {
    expect(validateAssistantConfig(tenantFixtures[0].assistants[0].config)).toEqual({});
  });

  it("reports duplicate names and invalid enum choices at dotted paths", () => {
    const config = structuredClone(tenantFixtures[0].assistants[0].config);
    config.services.boiler_repair.fields[1].name = "caller_name";
    config.services.boiler_repair.fields[4].choices = [];
    const errors = validateAssistantConfig(config);
    expect(errors["services.boiler_repair.fields.1.name"]).toMatch(/unique/);
    expect(errors["services.boiler_repair.fields.4.choices"]).toMatch(/at least one/);
  });

  it("checks office hours and date deadlines", () => {
    const config = structuredClone(tenantFixtures[0].assistants[0].config);
    config.office_hours.days = [];
    config.office_hours.closes = "07:00";
    config.urgency_rules[1] = { type: "date_deadline", service: "boiler_repair", field: "caller_name", add_months: 0, minus_days: -1, urgent_within_days: 0, reason: "Invalid date rule." };
    const errors = validateAssistantConfig(config);
    expect(errors["office_hours.days"]).toBeDefined();
    expect(errors["office_hours.closes"]).toMatch(/later/);
    expect(errors["urgency_rules.1.field"]).toMatch(/date field/);
    expect(errors["urgency_rules.1.minus_days"]).toBeDefined();
  });

  it("validates booking appointment keys, durations, and limits without coupling live actions", () => {
    const config = structuredClone(tenantFixtures[0].assistants[0].config);
    config.booking = {
      calendar_id: "primary",
      appointment_types: [
        { key: "initial_consultation", label: "Consultation", duration_minutes: 30, when_to_use: "New callers" },
        { key: "initial_consultation", label: "Follow-up", duration_minutes: 481, when_to_use: "Existing callers" },
      ],
      min_notice_hours: 2,
      horizon_days: 14,
      buffer_minutes: 15,
      max_slots_offered: 3,
      slot_step_minutes: 30,
      send_updates: "all",
    };
    const errors = validateAssistantConfig(config);

    expect(errors["booking.appointment_types.1.key"]).toMatch(/unique/);
    expect(errors["booking.appointment_types.1.duration_minutes"]).toMatch(/480/);
    expect(errors["booking.appointment_types.0.key"]).toBeUndefined();
    expect(config.live_actions).not.toContain("book_appointment");
  });
});
