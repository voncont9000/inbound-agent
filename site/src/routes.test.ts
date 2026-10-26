import { describe, expect, it } from "vitest";
import { matchRoute } from "./routes";

describe("matchRoute", () => {
  it("matches route paths with optional trailing slashes and index files", () => {
    expect(matchRoute("/").path).toBe("/");
    expect(matchRoute("/privacy/").path).toBe("/privacy");
    expect(matchRoute("/imprint/index.html").path).toBe("/imprint");
  });

  it("uses the not-found route for unknown paths", () => {
    expect(matchRoute("/unknown").path).toBe("/404");
  });
});
