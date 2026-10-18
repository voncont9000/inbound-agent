import { describe, expect, it } from "vitest";
import { followLabel, keyFromLabel, slugify, humanizeKey, uniqueKey } from "./keys";

describe("slugify", () => {
  it("turns labels into snake_case keys", () => {
    expect(slugify("Initial consultation (30 min)", "item")).toBe("initial_consultation_30_min");
    expect(slugify("  Duty   Solicitor! ", "item")).toBe("duty_solicitor");
  });

  it("strips accents and keeps ASCII only", () => {
    expect(slugify("Beratung für Straße", "item")).toBe("beratung_fur_strasse");
    expect(slugify("Café crème", "item")).toBe("cafe_creme");
  });

  it("always starts with a letter so field-name validation passes", () => {
    expect(slugify("2nd address", "field")).toBe("field_2nd_address");
    expect(slugify("2nd address", "field")).toMatch(/^[a-z][a-z0-9_]*$/);
  });

  it("falls back when the label has no usable characters", () => {
    expect(slugify("", "service")).toBe("service");
    expect(slugify("!!!", "service")).toBe("service");
    expect(slugify("Ärger 😀", "x")).toBe("arger");
  });

  it("limits key length", () => {
    expect(slugify("a".repeat(80), "x")).toHaveLength(48);
    expect(slugify(`${"a".repeat(47)} b`, "x")).toBe("a".repeat(47));
  });
});

describe("uniqueKey", () => {
  it("suffixes _2, _3 for collisions", () => {
    expect(uniqueKey("consultation", [])).toBe("consultation");
    expect(uniqueKey("consultation", ["consultation"])).toBe("consultation_2");
    expect(uniqueKey("consultation", ["consultation", "consultation_2"])).toBe("consultation_3");
  });

  it("combines with slugify", () => {
    expect(keyFromLabel("Boiler repair", ["boiler_repair"], "service")).toBe("boiler_repair_2");
  });
});

describe("followLabel", () => {
  it("regenerates the key of an unsaved item from its label", () => {
    expect(followLabel({ key: "service", label: "Boiler repair", taken: ["service", "other"], saved: ["other"], fallback: "service" })).toBe("boiler_repair");
  });

  it("does not collide with the item's own key or other keys", () => {
    expect(followLabel({ key: "co", label: "Co", taken: ["co"], saved: [], fallback: "x" })).toBe("co");
    expect(followLabel({ key: "new", label: "Other", taken: ["new", "other"], saved: ["other"], fallback: "x" })).toBe("other_2");
  });

  it("keeps saved keys stable when the label is edited", () => {
    expect(followLabel({ key: "consultation", label: "First meeting", taken: ["consultation"], saved: ["consultation"], fallback: "x" })).toBe("consultation");
  });
});

describe("humanizeKey", () => {
  it("turns stored keys into readable labels", () => {
    expect(humanizeKey("office_email")).toBe("Office email");
    expect(humanizeKey("action-1")).toBe("Action 1");
  });
});
