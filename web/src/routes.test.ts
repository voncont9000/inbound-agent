import { describe, expect, it } from "vitest";
import { routes } from "./routes";

describe("M1 route contracts", () => {
  it("lists every planned agent C route", () => {
    expect(routes.map(({ path }) => path)).toEqual([
      "/login",
      "/assistants/:id/settings",
      "/assistants/:id/knowledge",
      "/inbox",
      "/conversations/:id",
      "/contacts",
      "/contacts/:id",
      "/tasks",
      "/knowledge-gaps",
    ]);
  });
});
