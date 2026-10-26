import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { getAdminOverview } from "./adminApi";
import { ApiError } from "./errors";
import { server } from "../mocks/server";

describe("admin API", () => {
  it("throws ApiError with status and response body on an error response", async () => {
    server.use(http.get("*/api/admin/overview", () => HttpResponse.json({ detail: "Not found" }, { status: 404 })));
    await expect(getAdminOverview(7)).rejects.toMatchObject({
      name: "ApiError",
      message: "Not found",
      status: 404,
      body: { detail: "Not found" },
    } satisfies Partial<ApiError>);
  });
});
