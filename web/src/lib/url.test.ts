import { describe, expect, it } from "vitest";
import { businessNameFromWebsite, normalizeWebsiteUrl } from "./url";

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

describe("businessNameFromWebsite", () => {
  it.each([
    ["smith-dental.co.uk", "Smith Dental"],
    ["https://www.armford.com/x", "Armford"],
    ["shop.example.org", "Shop"],
    ["https://www.my_shop.example.org/contact", "My Shop"],
  ])("derives a business name from %s", (value, expected) => {
    expect(businessNameFromWebsite(value)).toBe(expected);
  });

  it.each(["", "https://", "not a url", "mailto:hello@example.org"])("returns an empty name for invalid input %s", (value) => {
    expect(businessNameFromWebsite(value)).toBe("");
  });
});
