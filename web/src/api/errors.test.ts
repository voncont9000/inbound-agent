import { describe, expect, it } from "vitest";
import { errorMessage, fieldErrorsFrom422 } from "./errors";

describe("API errors", () => {
  it("maps FastAPI validation locations to dotted field paths", () => {
    expect(fieldErrorsFrom422({ detail: [
      { loc: ["body", "config", "services", "employment", "fields", 0, "choices"], msg: "Choose one.", type: "value_error" },
      { loc: ["body", "name"], msg: "Required.", type: "value_error" },
    ] })).toEqual({
      "services.employment.fields.0.choices": "Choose one.",
      name: "Required.",
    });
  });

  it("uses a generic message for non-validation failures", () => {
    expect(fieldErrorsFrom422({ detail: "Unavailable" })).toEqual({});
    expect(errorMessage({ detail: "Unavailable" })).toBe("Unavailable");
    expect(errorMessage({ status: 500 })).toMatch(/went wrong/);
  });
});
