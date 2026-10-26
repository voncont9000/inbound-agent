import { describe, expect, it } from "vitest";
import { APP_URL } from "./config";
import { signupUrl } from "./signup";

describe("signupUrl", () => {
  it("omits a blank website", () => {
    expect(signupUrl("")).toBe(`${APP_URL}/signup`);
    expect(signupUrl("   ")).toBe(`${APP_URL}/signup`);
  });

  it("trims and encodes a website", () => {
    expect(signupUrl(" acme.com/a b?x=1&y=ü ")).toBe(
      `${APP_URL}/signup?website=${encodeURIComponent("acme.com/a b?x=1&y=ü")}`,
    );
  });
});
