import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Schemas } from "../api/client";
import { googleOAuthNavigation } from "../components/channelApi";
import { mockDb } from "../mocks/db";
import { server } from "../mocks/server";
import { renderApp } from "../test/renderApp";

const assistantId = "20000000-0000-4000-8000-000000000001";

function setupConnectRequests() {
  const order: string[] = [];
  let assistantUpdate: Schemas["AssistantUpdate"] | undefined;
  let oauthRequest: Schemas["GmailOAuthStartRequest"] | undefined;
  server.events.on("request:start", ({ request }) => {
    const pathname = new URL(request.url).pathname;
    if (request.method === "PUT" && pathname.endsWith(`/api/assistants/${assistantId}`)) {
      order.push("put");
      void request.clone().json().then((body) => {
        assistantUpdate = body as Schemas["AssistantUpdate"];
      });
    }
    if (request.method === "POST" && pathname.endsWith(`/api/assistants/${assistantId}/channels/gmail/oauth-start`)) {
      order.push("oauth");
    }
  });
  server.use(
    http.get("*/api/channels/gmail/mode", () => HttpResponse.json({ mode: "google" })),
    http.get("*/api/calendar/mode", () => HttpResponse.json({ mode: "google" })),
    http.get("*/api/assistants/:id/google-features", () => HttpResponse.json({ features: [], email: null })),
    http.post<{ id: string }, Schemas["GmailOAuthStartRequest"], { url: string }>(
      "*/api/assistants/:id/channels/gmail/oauth-start",
      async ({ request }) => {
        oauthRequest = await request.json() as Schemas["GmailOAuthStartRequest"];
        return HttpResponse.json({ url: "https://accounts.google.test/oauth" });
      },
    ),
  );
  return {
    order,
    get assistantUpdate() { return assistantUpdate; },
    get oauthRequest() { return oauthRequest; },
  };
}

function enableSavedBooking() {
  const assistant = mockDb.tenants[0].assistants.find(({ id }) => id === assistantId)!;
  const config = structuredClone(assistant.config);
  config.booking = {
    calendar_id: "primary",
    check_message: "Let me check the diary.",
    book_message: "One moment while I book that.",
    appointment_types: [{
      key: "consultation",
      label: "Consultation",
      duration_minutes: 30,
      when_to_use: "For callers requesting an initial consultation",
      location_or_link: null,
    }],
    min_notice_hours: 2,
    horizon_days: 14,
    buffer_minutes: 0,
    max_slots_offered: 3,
    slot_step_minutes: 30,
    send_updates: "all",
  };
  for (const action of ["check_availability", "book_appointment"] as const) {
    if (!config.live_actions.includes(action)) config.live_actions.push(action);
  }
  assistant.config = config;
}

afterEach(() => {
  server.events.removeAllListeners();
  vi.restoreAllMocks();
});

describe("Google OAuth from assistant abilities", () => {
  it("saves unsaved booking settings before starting Calendar OAuth and redirecting", async () => {
    const user = userEvent.setup();
    const requests = setupConnectRequests();
    const redirect = vi.spyOn(googleOAuthNavigation, "assign").mockImplementation(() => {
      requests.order.push("assign");
    });
    let releaseSave!: () => void;
    const saveGate = new Promise<void>((resolve) => { releaseSave = resolve; });
    server.use(http.put<{ id: string }, Schemas["AssistantUpdate"], Schemas["AssistantResponse"]>(
      "*/api/assistants/:id",
      async ({ request, params }) => {
        const body = await request.json() as Schemas["AssistantUpdate"];
        await saveGate;
        const tenant = mockDb.tenants[0];
        const assistantIndex = tenant.assistants.findIndex(({ id }) => id === params.id);
        const current = tenant.assistants[assistantIndex];
        const updated: Schemas["AssistantResponse"] = {
          ...current,
          name: body.name ?? current.name,
          template: body.template ?? current.template,
          config: body.config,
          version: current.version + 1,
          updated_at: new Date().toISOString(),
        };
        tenant.assistants[assistantIndex] = updated;
        return HttpResponse.json(updated);
      },
    ));
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("switch", { name: "Calendar booking" }));
    await user.click(await screen.findByRole("button", { name: "Connect Google Calendar" }));

    expect(await screen.findByRole("button", { name: "Saving changes…" })).toBeDisabled();
    expect(requests.order).toEqual(["put"]);
    releaseSave();

    await waitFor(() => expect(redirect).toHaveBeenCalledWith("https://accounts.google.test/oauth"));
    await waitFor(() => expect(requests.assistantUpdate).toBeDefined());
    expect(requests.assistantUpdate?.config.booking).not.toBeNull();
    expect(requests.assistantUpdate?.config.live_actions).toEqual(
      expect.arrayContaining(["check_availability", "book_appointment"]),
    );
    expect(requests.oauthRequest?.features).toEqual(["calendar"]);
    expect(requests.order).toEqual(["put", "oauth", "assign"]);
  });

  it("does not start Calendar OAuth when booking settings fail validation", async () => {
    const user = userEvent.setup();
    const requests = setupConnectRequests();
    const redirect = vi.spyOn(googleOAuthNavigation, "assign");
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("switch", { name: "Calendar booking" }));
    await user.clear(await screen.findByLabelText("Appointment label"));
    await user.click(await screen.findByRole("button", { name: "Connect Google Calendar" }));

    expect(await screen.findByText("Enter an appointment label.", { selector: ".field-error" })).toBeInTheDocument();
    expect(requests.order).toEqual([]);
    expect(redirect).not.toHaveBeenCalled();
  });

  it("starts Calendar OAuth without saving when the draft is clean", async () => {
    const user = userEvent.setup();
    enableSavedBooking();
    const requests = setupConnectRequests();
    const redirect = vi.spyOn(googleOAuthNavigation, "assign").mockImplementation(() => {
      requests.order.push("assign");
    });
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(await screen.findByRole("button", { name: "Connect Google Calendar" }));

    await waitFor(() => expect(redirect).toHaveBeenCalledWith("https://accounts.google.test/oauth"));
    expect(requests.order).toEqual(["oauth", "assign"]);
  });

  it("saves a new Sheets action before starting Sheets OAuth and redirecting", async () => {
    const user = userEvent.setup();
    const requests = setupConnectRequests();
    const redirect = vi.spyOn(googleOAuthNavigation, "assign").mockImplementation(() => {
      requests.order.push("assign");
    });
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: /After-conversation actions/ }));
    await user.click(screen.getByRole("switch", { name: "After-conversation actions" }));
    await user.click(screen.getByRole("button", { name: /Add a Google Sheets row/ }));
    await user.type(await screen.findByLabelText("Google Sheet link 1"), "https://docs.google.com/spreadsheets/d/sheet_123/edit");
    await user.click(await screen.findByRole("button", { name: "Connect Google Sheets" }));

    await waitFor(() => expect(redirect).toHaveBeenCalledWith("https://accounts.google.test/oauth"));
    await waitFor(() => expect(requests.assistantUpdate).toBeDefined());
    expect(requests.assistantUpdate?.config.post_actions).toEqual(
      expect.arrayContaining([expect.objectContaining({ kind: "sheets_row", spreadsheet_id: "sheet_123" })]),
    );
    expect(requests.oauthRequest?.features).toEqual(["sheets"]);
    expect(requests.order).toEqual(["put", "oauth", "assign"]);
  });
});
