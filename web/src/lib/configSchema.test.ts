import { describe, expect, it } from "vitest";
import assistantConfigSchema from "../mocks/assistant-config.schema.json";
import { enumAt, rangeAt } from "./configSchema";

describe("assistant config JSON schema options", () => {
  it("reads root options and nested FieldSpec options from the generated schema", () => {
    expect(enumAt(assistantConfigSchema, ["properties", "readback", "enum"], [])).toEqual(["off", "email", "critical"]);
    expect(enumAt(assistantConfigSchema, ["properties", "live_actions", "items", "enum"], [])).toEqual([
      "confirm_critical_fields",
      "flag_urgent",
      "request_callback",
      "log_complaint",
      "end_call",
      "check_availability",
      "book_appointment",
    ]);
    expect(enumAt(assistantConfigSchema, ["$defs", "FieldSpec", "properties", "type", "enum"], [])).toEqual([
      "string",
      "email",
      "phone",
      "date",
      "boolean",
      "integer",
      "number",
      "enum",
      "list",
    ]);
    expect(rangeAt(assistantConfigSchema)).toEqual({ min: 0, max: 1 });
  });
});
