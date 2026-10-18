import { describe, expect, it } from "vitest";
import { diffConfigs } from "./diff";

describe("diffConfigs", () => {
  it("reports added, removed, and changed dotted paths", () => {
    expect(diffConfigs(
      { business: { name: "A" }, services: ["call", "email"], removed: true },
      { business: { name: "B" }, services: ["call", "web"], added: 4 },
    )).toEqual([
      { path: "added", kind: "added", newValue: "4" },
      { path: "business.name", kind: "changed", oldValue: "\"A\"", newValue: "\"B\"" },
      { path: "removed", kind: "removed", oldValue: "true" },
      { path: "services.1", kind: "changed", oldValue: "\"email\"", newValue: "\"web\"" },
    ]);
  });

  it("returns no entries for equal values", () => {
    expect(diffConfigs({ a: [1, 2] }, { a: [1, 2] })).toEqual([]);
  });
});
