import { describe, expect, it } from "vitest";
import { routes } from "./routes";

describe("web route contracts", () => {
  it("lists every operator app route", () => {
    expect(routes.map(({ path }) => path)).toEqual([
      "/login",
      "/signup",
      "/assistants",
      "/assistants/new",
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
