import { describe, expect, it } from "vitest";
import { normalizeWebsiteUrl } from "./url";

describe("normalizeWebsiteUrl", () => {
  it.each([
    ["herefordssolicitors.co.uk", "https://herefordssolicitors.co.uk"],
    ["  www.x.co.uk/about ", "https://www.x.co.uk/about"],
    ["http://x.com", "http://x.com"],
    ["HTTPS://x.com", "HTTPS://x.com"],
    ["//x.com", "https://x.com"],
    ["", ""],
  ])("normalizes %s", (value, expected) => {
    expect(normalizeWebsiteUrl(value)).toBe(expected);
  });
});
