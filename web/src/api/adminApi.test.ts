import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { getAdminOverview } from "./adminApi";
import { ApiError } from "./errors";
import { server } from "../mocks/server";

describe("admin API", () => {
  it("throws ApiError with status and response body on an error response", async () => {
    server.use(http.get("*/api/admin/overview", () => HttpResponse.json({ detail: "Not Found" }, { status: 404 })));
    await expect(getAdminOverview(7)).rejects.toMatchObject({
      name: "ApiError",
      message: "Not Found",
      status: 404,
      body: { detail: "Not Found" },
    } satisfies Partial<ApiError>);
  });

  it("does not attach the selected workspace header to cross-workspace requests", async () => {
    const previousMemberships = localStorage.getItem("inbound.memberships");
    const previousTenant = localStorage.getItem("inbound.tenant");
    localStorage.setItem("inbound.memberships", JSON.stringify([{ id: "workspace-1" }, { id: "workspace-2" }]));
    localStorage.setItem("inbound.tenant", "workspace-2");
    let tenantHeader: string | null = "request not captured";
    const listener = ({ request }: { request: Request }) => {
      if (new URL(request.url).pathname === "/api/admin/overview") tenantHeader = request.headers.get("X-Tenant-Id");
    };
    server.events.on("request:start", listener);

    try {
      await getAdminOverview(7);
      expect(tenantHeader).toBeNull();
    } finally {
      server.events.removeListener("request:start", listener);
      if (previousMemberships === null) localStorage.removeItem("inbound.memberships");
      else localStorage.setItem("inbound.memberships", previousMemberships);
      if (previousTenant === null) localStorage.removeItem("inbound.tenant");
      else localStorage.setItem("inbound.tenant", previousTenant);
    }
  });
});
