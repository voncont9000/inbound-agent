import { http, HttpResponse } from "msw";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Schemas } from "../api/client";
import { server } from "../mocks/server";
import { mockDb } from "../mocks/db";
import { renderApp } from "../test/renderApp";
import { formatConversationDuration, groupConversationDays } from "./OperatorPages";

const assistantId = "20000000-0000-4000-8000-000000000001";
const conversationId = "50000000-0000-4000-8000-000000000001";
const transferConversationId = "50000000-0000-4000-8000-000000000011";
const contactId = "30000000-0000-4000-8000-000000000001";

describe("operator workflows", () => {
  it("adds and saves a transfer target with a generated key", async () => {
    const user = userEvent.setup();
    let capturedBody: Record<string, unknown> | undefined;
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "PUT" && request.url.includes("/assistants/")) {
        void request.clone().json().then((body) => { capturedBody = body as Record<string, unknown>; });
      }
    };
    server.events.on("request:start", listener);
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    expect(screen.queryByLabelText("key")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Call forwarding/ }));
    await user.click(screen.getByRole("switch", { name: "Call forwarding" }));
    await user.click(screen.getByRole("button", { name: "Add transfer target" }));
    await user.type(screen.getByLabelText("Name"), "Duty solicitor");
    const phone = screen.getByLabelText("Phone number");
    expect(phone).toHaveAttribute("type", "tel");
    await user.type(phone, "+447700900123");
    await user.type(screen.getByLabelText("When to use"), "The caller asks for a person.");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Forwarding messages" }));
    const handoffMessage = screen.getByLabelText("Handoff message");
    await user.clear(handoffMessage);
    await user.type(handoffMessage, "A colleague will speak with you shortly.");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(capturedBody).toBeDefined());
    const savedConfig = capturedBody?.config as Record<string, unknown>;
    expect(savedConfig.transfer_targets).toEqual([
      {
        key: "duty_solicitor",
        label: "Duty solicitor",
        phone: "+447700900123",
        when_to_use: "The caller asks for a person.",
        office_hours_only: true,
      },
    ]);
    expect(savedConfig.live_actions).toContain("transfer_call");
    expect(savedConfig.transfer_failed_message).toBe(
      "Sorry, nobody is available to take your call right now.",
    );
    expect(savedConfig.transfer_handoff_message).toBe(
      "A colleague will speak with you shortly.",
    );
    server.events.removeListener("request:start", listener);
  }, 15000);

  it("edits and saves the email triage criteria", async () => {
    const user = userEvent.setup();
    let capturedBody: Record<string, unknown> | undefined;
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "PUT" && request.url.includes("/assistants/")) {
        void request.clone().json().then((body) => { capturedBody = body as Record<string, unknown>; });
      }
    };
    server.events.on("request:start", listener);
    renderApp(`/assistants/${assistantId}/settings/channels`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });

    const needsPerson = screen.getByLabelText("Needs a person");
    await user.clear(needsPerson);
    await user.type(needsPerson, "Escalate complaints and urgent requests to the team.");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(capturedBody).toBeDefined());
    const savedConfig = capturedBody?.config as Record<string, unknown>;
    expect(savedConfig.email_triage).toEqual(expect.objectContaining({
      needs_human: "Escalate complaints and urgent requests to the team.",
    }));
    server.events.removeListener("request:start", listener);
  }, 15000);

  it("shows transfer metrics and failed transfer task on conversation details", async () => {
    const transferConversation = mockDb.tenants[1].conversations[0];
    server.use(
      http.get<{ id: string }, never, Schemas["ConversationDetailResponse"]>(
        "*/api/conversations/:id",
        ({ params }) => HttpResponse.json({ ...transferConversation, id: params.id }),
      ),
    );
    renderApp(`/conversations/${transferConversationId}`, { loggedIn: true });
    await screen.findByText(/Enquiry about unfair dismissal/, { selector: ".conversation-summary-strip p" });

    const transfer = screen.getByRole("heading", { name: "Transfer" }).parentElement;
    expect(transfer).toHaveTextContent("duty_solicitor");
    expect(transfer).toHaveTextContent("no-answer");
    expect(transfer).toHaveTextContent("1200 ms");
    expect(screen.getByRole("heading", { name: "Tasks" }).parentElement).toHaveTextContent(
      "transfer_failed",
    );
  });

  it("validates settings across tabs, saves the complete config, and shows server validation paths", async () => {
    const user = userEvent.setup();
    const initialConfig = structuredClone(mockDb.tenants[0].assistants[0].config);
    let putCount = 0;
    let capturedBody: Record<string, unknown> | undefined;
    const putListener = ({ request }: { request: Request }) => {
      if (request.method === "PUT" && request.url.includes("/assistants/")) {
        putCount += 1;
        void request.clone().json().then((body) => { capturedBody = body as Record<string, unknown>; });
      }
    };
    server.events.on("request:start", putListener);
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: "Edit services and intake fields" }));
    expect(screen.queryByLabelText("Field name")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Service key")).not.toBeInTheDocument();

    await user.clear(screen.getAllByLabelText("Choices")[0]);
    await user.click(screen.getByRole("link", { name: "Abilities" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(screen.getByTestId("current-location")).toHaveTextContent(`/assistants/${assistantId}/settings/general`));
    expect(await screen.findByRole("heading", { name: /errors to fix/ })).toBeInTheDocument();
    expect(screen.getAllByLabelText("Choices")[0]).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("link", { name: /General.*1 to fix/ })).toBeInTheDocument();
    expect(putCount).toBe(0);

    await user.type(screen.getAllByLabelText("Choices")[0], "a, b");
    const serviceLabel = screen.getAllByLabelText("Service label")[0];
    await user.clear(serviceLabel);
    await user.type(serviceLabel, "Boiler servicing");
    await user.click(screen.getByRole("link", { name: "Rules" }));
    await user.clear(screen.getByLabelText("Phrases (comma or newline separated)"));
    await user.type(screen.getByLabelText("Phrases (comma or newline separated)"), "gas leak, flooding");
    await user.click(screen.getByRole("link", { name: "Abilities" }));
    await user.click(screen.getByRole("button", { name: /Readback/ }));
    if (!(screen.getByRole("switch", { name: "Readback" }) as HTMLInputElement).checked) await user.click(screen.getByRole("switch", { name: "Readback" }));
    await user.click(screen.getByRole("radio", { name: /Read email addresses back letter by letter/ }));
    await user.click(screen.getByRole("radio", { name: /Read back every field marked critical/ }));
    await user.click(screen.getByRole("link", { name: "Advanced" }));
    expect(screen.queryByRole("radio", { name: /Read back every field marked critical/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /transfer call/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: /request callback/ }));
    const threshold = screen.getByRole("slider", { name: /confidence threshold/i });
    fireEvent.change(threshold, { target: { value: "0.8" } });

    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(capturedBody).toBeDefined());
    expect(await screen.findByText("Changes saved")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled());
    const savedConfig = capturedBody?.config as Record<string, unknown>;
    expect(savedConfig.readback).toBe("critical");
    expect(savedConfig.live_actions).toContain("request_callback");
    expect(savedConfig.review_confidence_threshold).toBe(0.8);
    expect(savedConfig.knowledge).toEqual(initialConfig.knowledge);
    expect(savedConfig.price_list).toEqual(initialConfig.price_list);
    const savedServices = savedConfig.services as Record<string, { label: string; fields: Array<Record<string, unknown>> }>;
    expect(Object.keys(savedServices)).toEqual(Object.keys(initialConfig.services));
    expect(savedServices.boiler_repair.label).toBe("Boiler servicing");
    expect(savedServices.boiler_repair.fields[4].choices).toEqual(["a", "b"]);
    expect(savedServices.boiler_repair.fields.map(({ name }) => name)).toEqual(initialConfig.services.boiler_repair.fields.map(({ name }) => name));
    expect((savedConfig.urgency_rules as Array<Record<string, unknown>>).find(({ type }) => type === "keyword")?.phrases).toEqual(["gas leak", "flooding"]);
    expect(savedConfig.urgency_rules).toHaveLength(initialConfig.urgency_rules.length);
    for (const service of Object.values(savedServices)) {
      for (const field of service.fields) {
        if (field.type !== "enum") expect(field).not.toHaveProperty("choices");
      }
    }

    server.use(http.put<{ id: string }, Schemas["AssistantUpdate"], Schemas["HTTPValidationError"]>("*/api/assistants/:id", () => HttpResponse.json<Schemas["HTTPValidationError"]>({
      detail: [{ loc: ["body", "config", "services", "boiler_repair", "fields", 0, "label"], msg: "Label rejected by server.", type: "value_error" }],
    }, { status: 422 })));
    await user.click(screen.getByRole("link", { name: "General" }));
    await user.type(screen.getByRole("textbox", { name: "Greeting" }), " Welcome.");
    await user.click(screen.getByRole("link", { name: "Knowledge" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(screen.getByTestId("current-location")).toHaveTextContent(`/assistants/${assistantId}/settings/general`));
    await waitFor(() => expect(screen.getAllByText("Label rejected by server.")).toHaveLength(2));
    const firstLabel = screen.getAllByLabelText("Field label")[0];
    expect(firstLabel).toHaveAttribute("aria-invalid", "true");
    expect(document.getElementById(firstLabel.getAttribute("aria-describedby")?.split(" ")[0] ?? "")).toHaveTextContent("Label rejected by server.");
    server.events.removeListener("request:start", putListener);
  }, 20000);

  it("generates unique keys for new services and fields and keeps them stable after saving", async () => {
    const user = userEvent.setup();
    let capturedBody: Record<string, unknown> | undefined;
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "PUT" && request.url.includes("/assistants/")) {
        void request.clone().json().then((body) => { capturedBody = body as Record<string, unknown>; });
      }
    };
    server.events.on("request:start", listener);
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: "Edit services and intake fields" }));
    await user.click(screen.getByRole("button", { name: "Add service" }));
    const labels = screen.getAllByLabelText("Service label");
    const newService = labels[labels.length - 1];
    await user.type(newService, "Boiler repair");
    const addFieldButtons = screen.getAllByRole("button", { name: "Add field" });
    await user.click(addFieldButtons[addFieldButtons.length - 1]);
    const fieldLabels = screen.getAllByLabelText("Field label");
    await user.type(fieldLabels[fieldLabels.length - 1], "Caller name");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(capturedBody).toBeDefined());
    const services = (capturedBody?.config as { services: Record<string, { label: string; fields: Array<{ name: string; label: string }> }> }).services;
    expect(services.boiler_repair_2).toMatchObject({ label: "Boiler repair", fields: [{ name: "caller_name", label: "Caller name" }] });
    expect(await screen.findByText("Changes saved")).toBeInTheDocument();

    capturedBody = undefined;
    const savedLabels = screen.getAllByLabelText("Service label");
    await user.type(savedLabels[savedLabels.length - 1], " and servicing");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(capturedBody).toBeDefined());
    const renamed = ((capturedBody as Record<string, unknown> | undefined)?.config as { services: Record<string, { label: string }> }).services;
    expect(renamed.boiler_repair_2.label).toBe("Boiler repair and servicing");
    server.events.removeListener("request:start", listener);
  }, 20000);

  it("configures an appointment type and saves booking settings", async () => {
    const user = userEvent.setup();
    let savedConfig: Record<string, unknown> | undefined;
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "PUT" && request.url.includes("/assistants/")) {
        void request.clone().json().then((body) => { savedConfig = (body as { config: Record<string, unknown> }).config; });
      }
    };
    server.events.on("request:start", listener);
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    expect(screen.queryByLabelText("Duration (minutes)")).not.toBeInTheDocument();
    await user.click(screen.getByRole("switch", { name: "Calendar booking" }));
    expect(screen.queryByLabelText("Key")).not.toBeInTheDocument();
    const duration = screen.getByLabelText("Duration (minutes)");
    await user.clear(duration);
    await user.type(duration, "45");
    await user.click(screen.getByRole("button", { name: "Add appointment type" }));
    const labels = screen.getAllByLabelText("Appointment label");
    await user.clear(labels[1]);
    await user.type(labels[1], "Consultation");
    await user.type(screen.getAllByLabelText("When to use this appointment type")[1], "Follow-up visits.");
    await user.click(screen.getByRole("button", { name: "Booking timing and messages" }));
    const checkMessage = screen.getByLabelText("Availability holding message");
    await user.clear(checkMessage);
    await user.type(checkMessage, "Checking the diary now.");
    const bookMessage = screen.getByLabelText("Booking holding message");
    await user.clear(bookMessage);
    await user.type(bookMessage, "Booking that now.");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(savedConfig).toBeDefined());

    expect(savedConfig?.booking).toMatchObject({
      calendar_id: "primary",
      check_message: "Checking the diary now.",
      book_message: "Booking that now.",
      appointment_types: [{ key: "consultation", duration_minutes: 45 }, { key: "consultation_2", label: "Consultation" }],
    });
    expect(savedConfig?.live_actions).toEqual(expect.arrayContaining(["check_availability", "book_appointment"]));
    server.events.removeListener("request:start", listener);
  }, 15000);

  it("keeps booking appointment types when turning booking off is cancelled", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    let savedConfig: Record<string, unknown> | undefined;
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "PUT" && request.url.includes("/assistants/")) {
        void request.clone().json().then((body) => {
          savedConfig = (body as { config: Record<string, unknown> }).config;
        });
      }
    };
    server.events.on("request:start", listener);
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("switch", { name: "Calendar booking" }));
    await user.click(screen.getByRole("button", { name: "Add appointment type" }));
    await user.type(
      screen.getAllByLabelText("When to use this appointment type")[1],
      "Initial consultation.",
    );

    await user.click(screen.getByRole("switch", { name: "Calendar booking" }));
    expect(confirm).toHaveBeenCalledWith(
      "Turning off calendar booking removes its 2 appointment type(s). Continue?",
    );
    expect(screen.getByRole("switch", { name: "Calendar booking" })).toBeChecked();
    expect(screen.getAllByLabelText("Appointment label")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(savedConfig).toBeDefined());
    expect(
      (savedConfig?.booking as { appointment_types: unknown[] }).appointment_types,
    ).toHaveLength(2);
    confirm.mockRestore();
    server.events.removeListener("request:start", listener);
  }, 15000);

  it("clears booking after confirming the removal of appointment types", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    let savedConfig: Record<string, unknown> | undefined;
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "PUT" && request.url.includes("/assistants/")) {
        void request.clone().json().then((body) => {
          savedConfig = (body as { config: Record<string, unknown> }).config;
        });
      }
    };
    server.events.on("request:start", listener);
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("switch", { name: "Calendar booking" }));
    await user.click(screen.getByRole("button", { name: "Add appointment type" }));

    await user.click(screen.getByRole("switch", { name: "Calendar booking" }));
    expect(confirm).toHaveBeenCalledWith(
      "Turning off calendar booking removes its 2 appointment type(s). Continue?",
    );
    expect(screen.getByRole("switch", { name: "Calendar booking" })).not.toBeChecked();
    expect(screen.queryByLabelText("Appointment label")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(savedConfig).toBeDefined());
    expect(savedConfig?.booking).toBeNull();
    confirm.mockRestore();
    server.events.removeListener("request:start", listener);
  }, 15000);

  it("requests Calendar OAuth scopes and warns when the calendar is disconnected", async () => {
    const user = userEvent.setup();
    let oauthFeatures: string[] | undefined;
    server.use(
      http.get("*/api/channels/gmail/mode", () => HttpResponse.json({ mode: "google" })),
      http.get("*/api/calendar/mode", () => HttpResponse.json({ mode: "google" })),
      http.post<{ id: string }, Schemas["GmailOAuthStartRequest"], Schemas["HTTPValidationError"]>(
        "*/api/assistants/:id/channels/gmail/oauth-start",
        async ({ request }) => {
          oauthFeatures = (await request.json() as Schemas["GmailOAuthStartRequest"]).features;
          return HttpResponse.json<Schemas["HTTPValidationError"]>({
            detail: [{ loc: ["body"], msg: "OAuth unavailable", type: "value_error" }],
          }, { status: 503 });
        },
      ),
    );
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("switch", { name: "Calendar booking" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/calendar is disconnected/i);
    await user.click(screen.getByRole("button", { name: "Connect Google Calendar" }));
    await waitFor(() => expect(oauthFeatures).toEqual(["calendar"]));
  });

  it("hides Google Calendar connect once the assistant already has calendar access", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("*/api/channels/gmail/mode", () => HttpResponse.json({ mode: "google" })),
      http.get("*/api/calendar/mode", () => HttpResponse.json({ mode: "google" })),
      http.get<{ id: string }, never, Schemas["ChannelConnectionsResponse"]>(
        "*/api/assistants/:id/channels",
        () => HttpResponse.json({
          items: [{
            id: "gmail-calendar-connection",
            assistant_id: assistantId,
            kind: "gmail",
            external_id: "calendar@example.test",
            status: "active",
            settings: {},
            created_at: "2026-06-01T00:00:00Z",
            google_features: ["calendar"],
          }],
        }),
      ),
    );
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("switch", { name: "Calendar booking" }));
    expect(await screen.findByText("Google Calendar is connected for this assistant.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect Google Calendar" })).not.toBeInTheDocument();
  });

  it("shows fake Calendar connection status from channel features without an OAuth button", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("*/api/channels/gmail/mode", () => HttpResponse.json({ mode: "fake" })),
      http.get("*/api/calendar/mode", () => HttpResponse.json({ mode: "fake" })),
      http.get<{ id: string }, never, Schemas["ChannelConnectionsResponse"]>(
        "*/api/assistants/:id/channels",
        () => HttpResponse.json({
          items: [{
            id: "fake-calendar-connection",
            assistant_id: assistantId,
            kind: "gmail",
            external_id: "calendar@example.test",
            status: "active",
            settings: {},
            created_at: "2026-06-01T00:00:00Z",
            google_features: ["calendar"],
          }],
        }),
      ),
    );
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("switch", { name: "Calendar booking" }));
    expect(await screen.findByText(/Google Calendar is connected for this assistant/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect Google Calendar" })).not.toBeInTheDocument();
  });

  it("tests an after-conversation action and shows the rendered result", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: /After-conversation actions/ }));
    await user.click(screen.getByRole("switch", { name: "After-conversation actions" }));
    await user.selectOptions(screen.getByLabelText("Add action"), "email_customer");
    await user.click(screen.getByRole("button", { name: "Send test" }));
    expect(await screen.findByText("Ready to send.")).toBeInTheDocument();
    expect(screen.getByText(/Thanks for contacting Brightside Plumbing/)).toBeInTheDocument();
  });

  it("offers Google Sheets connection when a sheet action has no Sheets scope", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: /After-conversation actions/ }));
    await user.click(screen.getByRole("switch", { name: "After-conversation actions" }));
    await user.selectOptions(screen.getByLabelText("Add action"), "sheets_row");
    expect(await screen.findByRole("button", { name: "Connect Google Sheets" })).toBeInTheDocument();
  });

  it("appends created assistants and keeps the existing assistant state", async () => {
    const user = userEvent.setup();
    const existingAssistantId = mockDb.tenants[0].assistants[0].id;
    renderApp("/assistants", { loggedIn: true });
    await screen.findByRole("heading", { name: "Assistants" });
    await user.click(screen.getByRole("button", { name: "Create from template" }));
    await user.click(await screen.findByRole("radio", { name: /home services/i }));
    await user.type(screen.getByLabelText("Assistant name"), "Second assistant");
    await user.click(screen.getByRole("button", { name: "Create assistant" }));

    await screen.findByRole("heading", { name: "Second assistant" });
    const tenant = mockDb.tenants[0];
    expect(tenant.assistants).toHaveLength(2);
    expect(tenant.assistants[0].id).toBe(existingAssistantId);
    expect(tenant.assistants[1].name).toBe("Second assistant");
    expect(tenant.assistantData[existingAssistantId].versions).toHaveLength(3);
    expect(tenant.assistantData[tenant.assistants[1].id].versions).toHaveLength(1);
    await user.click(screen.getByRole("link", { name: "Assistants" }));
    expect(await screen.findByRole("heading", { name: "Brightside reception" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Second assistant" })).toBeInTheDocument();
  });

  it("resolves a review conflict only after explicit caller-confirmation override", async () => {
    const user = userEvent.setup();
    let reviewBody: unknown;
    const listener = ({ request }: { request: Request }) => {
      if (request.url.includes("/reviews")) void request.clone().json().then((body) => { reviewBody = body; });
    };
    server.events.on("request:start", listener);
    renderApp(`/conversations/${conversationId}`, { loggedIn: true });
    await screen.findByText(/Boiler not firing, no hot water/, { selector: ".conversation-summary-strip p" });
    expect(screen.getByText("Test call", { selector: ".badge" })).toBeInTheDocument();
    expect(screen.getByText(/staff-alert · notify_staff/)).toBeInTheDocument();
    const intakeTable = screen.getByRole("table", { name: "Intake comparison by field" });
    expect(within(intakeTable).getAllByRole("columnheader").map((header) => header.textContent)).toEqual(["Field", "Live", "Post-call", "Merged"]);
    expect(within(intakeTable).getAllByRole("row")).toHaveLength(4);
    expect(within(intakeTable).getByRole("rowheader", { name: "caller_name" })).toBeInTheDocument();
    expect(screen.getAllByText("Jamie M.").length).toBeGreaterThan(0);
    expect(screen.getAllByText("live (conflict)", { exact: true }).length).toBeGreaterThan(0);

    await user.click(screen.getAllByRole("button", { name: "Review field" })[0]);
    await user.click(screen.getByRole("radio", { name: "Post-call: Jamie M." }));
    const submit = screen.getByRole("button", { name: "Submit review" });
    expect(submit).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "I'm overriding a value the caller confirmed" }));
    expect(submit).toBeEnabled();
    await user.type(screen.getByLabelText("Note (optional)"), "Verified with the caller.");
    await user.click(submit);
    await waitFor(() => expect(reviewBody).toEqual({ field: "caller_name", resolved_value: "Jamie M.", note: "Verified with the caller." }));
    expect(await screen.findByText("human")).toBeInTheDocument();
    expect(screen.queryByText("Needs review")).not.toBeInTheDocument();
    expect(screen.getAllByText("Jamie Morgan").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Jamie M.").length).toBeGreaterThan(0);
    server.events.removeListener("request:start", listener);
  });

  it("renders call transcript rows with speaker labels and elapsed time", async () => {
    renderApp(`/conversations/${conversationId}`, { loggedIn: true });
    const transcript = await screen.findByRole("list", { name: "Conversation transcript" });
    const rows = within(transcript).getAllByRole("listitem");

    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveAttribute("data-role", "caller");
    expect(within(rows[0]).getByText("Caller")).toBeInTheDocument();
    expect(within(rows[0]).getByText("0:00")).toBeInTheDocument();
    expect(rows[1]).toHaveAttribute("data-role", "caller");
    expect(within(rows[1]).getByText("Caller")).toBeInTheDocument();
    expect(within(rows[1]).getByText("0:05")).toBeInTheDocument();
    expect(rows[2]).toHaveAttribute("data-role", "assistant");
    expect(within(rows[2]).getByText("Assistant")).toBeInTheDocument();
    expect(within(rows[2]).getByText("0:18")).toBeInTheDocument();
  });

  it("updates inbox URL filters and sends channel plus review filters", async () => {
    const user = userEvent.setup();
    const requested: URL[] = [];
    const listener = ({ request }: { request: Request }) => {
      if (request.url.includes("/conversations?")) requested.push(new URL(request.url));
    };
    server.events.on("request:start", listener);
    renderApp("/inbox", { loggedIn: true });
    await screen.findByRole("heading", { name: "Inbox" });
    await user.click(screen.getByRole("button", { name: /Filters/ }));
    await user.selectOptions(screen.getByLabelText("Channel"), "phone");
    await user.click(screen.getByLabelText("Needs review only"));
    await waitFor(() => expect(requested.some((url) => url.searchParams.get("channel") === "phone" && url.searchParams.get("has_review") === "true")).toBe(true));
    await waitFor(() => expect(screen.getByTestId("current-location")).toHaveTextContent("/inbox?channel=phone&has_review=true"));
    const list = screen.getByRole("region", { name: "Conversation list" });
    await waitFor(() => expect(within(list).getAllByRole("article")).toHaveLength(1));
    expect(screen.getAllByText("Needs review").length).toBeGreaterThan(0);
    const rows = within(list).getAllByRole("article");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("Phone");
    expect(rows[0]).toHaveTextContent("Jamie Morgan");
    server.events.removeListener("request:start", listener);
  });

  it("closes inbox filters on Escape and returns focus to the Filters button", async () => {
    const user = userEvent.setup();
    renderApp("/inbox", { loggedIn: true });
    await screen.findByRole("heading", { name: "Inbox" });
    const trigger = screen.getByRole("button", { name: /Filters/ });

    await user.click(trigger);
    expect(await screen.findByLabelText("Channel")).toBeInTheDocument();
    await user.keyboard("{Escape}");

    expect(screen.queryByLabelText("Channel")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("closes inbox filters on outside click without moving focus from the clicked control", async () => {
    const user = userEvent.setup();
    renderApp("/inbox", { loggedIn: true });
    await screen.findByRole("heading", { name: "Inbox" });
    await user.click(screen.getByRole("button", { name: /Filters/ }));
    expect(await screen.findByLabelText("Channel")).toBeInTheDocument();

    const search = screen.getByRole("searchbox", { name: "Search conversations" });
    await user.click(search);

    expect(screen.queryByLabelText("Channel")).not.toBeInTheDocument();
    expect(search).toHaveFocus();
  });

  it("marks test calls in the inbox", async () => {
    renderApp("/inbox", { loggedIn: true });
    await screen.findByRole("heading", { name: "Inbox" });
    expect(await screen.findAllByText("Test call")).not.toHaveLength(0);
  });

  it("searches the loaded inbox rows by contact", async () => {
    const user = userEvent.setup();
    renderApp("/inbox", { loggedIn: true });
    await screen.findByRole("heading", { name: "Inbox" });
    await user.type(screen.getByLabelText("Search conversations"), "Boiler not firing");
    const list = screen.getByRole("region", { name: "Conversation list" });
    await waitFor(() => expect(within(list).getAllByRole("article")).toHaveLength(1));
    expect(within(list).getByText("Boiler not firing, no hot water since this morning")).toBeInTheDocument();
    expect(within(list).getByText("1 results")).toBeInTheDocument();
  });

  it("marks Gmail assistant messages as drafts", async () => {
    const conversation = structuredClone(mockDb.tenants[0].conversations[0]);
    conversation.channel = "gmail";
    conversation.metrics = {
      ...conversation.metrics,
      email_triage: { category: "needs_human", reason: "A person should review this enquiry." },
    };
    conversation.messages.push({
      id: `${conversationId}-draft`,
      role: "assistant",
      text: "Thanks for your enquiry.",
      ts: new Date().toISOString(),
      meta: { draft: true, gmail_draft_id: "draft-1" },
    });
    server.use(http.get<{ id: string }, never, Schemas["ConversationDetailResponse"]>(
      "*/api/conversations/:id",
      () => HttpResponse.json<Schemas["ConversationDetailResponse"]>(conversation),
    ));
    renderApp(`/conversations/${conversationId}`, { loggedIn: true });
    expect(await screen.findByText("Draft in Gmail")).toBeInTheDocument();
    const triage = screen.getByRole("heading", { name: "Email triage" }).parentElement;
    expect(triage).toHaveTextContent("needs_human");
    expect(triage).toHaveTextContent("A person should review this enquiry.");
  });

  it("shows booking details from conversation metrics", async () => {
    const conversation = structuredClone(mockDb.tenants[0].conversations[0]);
    conversation.metrics = {
      ...conversation.metrics,
      booking: {
        appointment_type: "consultation",
        label: "Consultation",
        text: "Tuesday 2 June at 10:00",
        status: "booked",
        start: "2026-06-02T09:00:00.000Z",
        timezone: "Europe/London",
        attendee_name: "Alex Example",
        attendee_email: "alex@example.test",
        event_link: "https://calendar.example/event",
      },
    };
    server.use(http.get<{ id: string }, never, Schemas["ConversationDetailResponse"]>(
      "*/api/conversations/:id",
      () => HttpResponse.json<Schemas["ConversationDetailResponse"]>(conversation),
    ));
    renderApp(`/conversations/${conversationId}`, { loggedIn: true });
    expect(await screen.findByRole("heading", { name: "Appointment booking" })).toBeInTheDocument();
    expect(screen.getByText("Tuesday 2 June at 10:00")).toBeInTheDocument();
    expect(screen.getByText(/2 June 2026 at 10:00/)).toBeInTheDocument();
    expect(screen.getByText(/Alex Example · alex@example.test/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Calendar event" })).toHaveAttribute("href", "https://calendar.example/event");
  });

  it("sends task filters to the server", async () => {
    const user = userEvent.setup();
    const requested: URL[] = [];
    const listener = ({ request }: { request: Request }) => {
      if (request.url.includes("/tasks?")) requested.push(new URL(request.url));
    };
    server.events.on("request:start", listener);
    renderApp("/tasks", { loggedIn: true });
    await screen.findByRole("heading", { name: "Tasks" });
    await user.selectOptions(screen.getByLabelText("Status"), "open");
    await user.type(screen.getByLabelText("Kind"), "callback");
    await screen.findByRole("option", { name: "Brightside reception" });
    await user.selectOptions(screen.getByLabelText("Assistant"), assistantId);
    await waitFor(() =>
      expect(
        requested.some(
          (url) =>
            url.searchParams.get("status") === "open"
            && url.searchParams.get("kind") === "callback"
            && url.searchParams.get("assistant_id") === assistantId,
        ),
      ).toBe(true),
    );
    server.events.removeListener("request:start", listener);
  });

  it("sends knowledge-gap filters to the server", async () => {
    const user = userEvent.setup();
    const requested: URL[] = [];
    const listener = ({ request }: { request: Request }) => {
      if (request.url.includes("/knowledge-gaps?")) requested.push(new URL(request.url));
    };
    server.events.on("request:start", listener);
    renderApp("/knowledge-gaps", { loggedIn: true });
    await screen.findByRole("heading", { name: "Knowledge gaps" });
    await user.selectOptions(screen.getByLabelText("Status"), "answered");
    await screen.findByRole("option", { name: "Brightside reception" });
    await user.selectOptions(screen.getByLabelText("Assistant"), assistantId);
    await waitFor(() =>
      expect(
        requested.some(
          (url) =>
            url.searchParams.get("status") === "answered"
            && url.searchParams.get("assistant_id") === assistantId,
        ),
      ).toBe(true),
    );
    server.events.removeListener("request:start", listener);
  });

  it("shows muted empty states for contact sections and conversation tasks", async () => {
    const contact = mockDb.tenants[0].contacts[0];
    const conversation = mockDb.tenants[0].conversations[0];
    server.use(
      http.get<{ id: string }, never, Schemas["ContactDetailResponse"]>("*/api/contacts/:id", ({ params }) =>
        HttpResponse.json<Schemas["ContactDetailResponse"]>({
          ...contact,
          id: params.id,
          identities: [],
          active_facts: [],
          conversations: [],
          open_tasks: [],
          open_commitments: [],
          context_card: "",
        }),
      ),
      http.get<{ id: string }, never, Schemas["ConversationDetailResponse"]>("*/api/conversations/:id", ({ params }) =>
        HttpResponse.json<Schemas["ConversationDetailResponse"]>({ ...conversation, id: params.id, tasks: [] }),
      ),
    );

    const contactView = renderApp(`/contacts/${contactId}`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Jamie Morgan" });
    expect(screen.getByText("No identities yet.")).toBeInTheDocument();
    expect(screen.getByText("No facts yet.")).toBeInTheDocument();
    expect(screen.getByText("No conversations yet.")).toBeInTheDocument();
    expect(screen.getByText("No open tasks.")).toBeInTheDocument();
    expect(screen.getByText("No open commitments.")).toBeInTheDocument();
    expect(screen.getByText("No context card yet.")).toBeInTheDocument();
    contactView.unmount();

    renderApp(`/conversations/${conversationId}`, { loggedIn: true });
    await screen.findByText("No tasks.");
  });

  it("shows merge confirmation and sends no merge request until confirmed", async () => {
    const user = userEvent.setup();
    let mergeBody: unknown;
    let mergeCount = 0;
    let contactsListRequests = 0;
    let currentContactRequests = 0;
    const queryListener = ({ request }: { request: Request }) => {
      const url = new URL(request.url);
      if (request.method === "GET" && url.pathname.endsWith("/contacts")) contactsListRequests += 1;
      if (request.method === "GET" && url.pathname.endsWith(`/contacts/${contactId}`)) currentContactRequests += 1;
    };
    server.events.on("request:start", queryListener);
    server.use(http.post<{ id: string }, Schemas["ContactMergeRequest"], Schemas["ContactMergeResponse"]>("*/api/contacts/:id/merge", async ({ request, params }) => {
      mergeCount += 1;
      mergeBody = await request.json() as Schemas["ContactMergeRequest"];
      return HttpResponse.json<Schemas["ContactMergeResponse"]>({ survivor_id: String(params.id), merged_id: "30000000-0000-4000-8000-000000000002" });
    }));
    renderApp(`/contacts/${contactId}`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Jamie Morgan" });
    await user.selectOptions(screen.getByLabelText("Merge another contact into this one"), "30000000-0000-4000-8000-000000000002");
    await user.click(screen.getByRole("button", { name: "Merge contact" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/Identities, facts and conversations move/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(mergeCount).toBe(0);
    await user.click(screen.getByRole("button", { name: "Merge contact" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Merge" }));
    await waitFor(() => expect(mergeBody).toEqual({ other_id: "30000000-0000-4000-8000-000000000002" }));
    await waitFor(() => expect(contactsListRequests).toBeGreaterThan(1));
    await waitFor(() => expect(currentContactRequests).toBeGreaterThan(1));
    expect(screen.getByTestId("current-location")).toHaveTextContent(`/contacts/${contactId}`);
    expect(screen.getByRole("heading", { name: "Jamie Morgan" })).toBeInTheDocument();
    server.events.removeListener("request:start", queryListener);
  });

  it("confirms a split, sends selected identities, and navigates to the returned contact", async () => {
    const user = userEvent.setup();
    const splitId = "30000000-0000-4000-8000-000000000099";
    const contact = mockDb.tenants[0].contacts[0];
    const identityId = contact.identities[0].id;
    let splitBody: unknown;
    let currentContactRequests = 0;
    const queryListener = ({ request }: { request: Request }) => {
      const url = new URL(request.url);
      if (request.method === "GET" && url.pathname.endsWith(`/contacts/${contactId}`)) currentContactRequests += 1;
    };
    server.events.on("request:start", queryListener);
    server.use(
      http.post<{ id: string }, Schemas["ContactSplitRequest"], Schemas["ContactSplitResponse"]>("*/api/contacts/:id/split", async ({ request, params }) => {
        splitBody = await request.json();
        return HttpResponse.json<Schemas["ContactSplitResponse"]>({ person_id: params.id, split_person_id: splitId });
      }),
      http.get<{ id: string }, never, Schemas["ContactDetailResponse"]>("*/api/contacts/:id", ({ params }) =>
        HttpResponse.json<Schemas["ContactDetailResponse"]>({
          ...contact,
          id: params.id,
          display_name: params.id === splitId ? `${contact.display_name} (split)` : contact.display_name,
        }),
      ),
    );
    renderApp(`/contacts/${contactId}`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Jamie Morgan" });
    await user.click(screen.getAllByRole("checkbox")[0]);
    await user.click(screen.getByRole("button", { name: "Split selected identities" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("The selected identities will move to a new contact profile.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Split" }));
    await waitFor(() => expect(splitBody).toEqual({ identity_ids: [identityId] }));
    expect(await screen.findByTestId("current-location")).toHaveTextContent(`/contacts/${splitId}`);
    await waitFor(() => expect(currentContactRequests).toBeGreaterThan(1));
    server.events.removeListener("request:start", queryListener);
  });

  it("bulk deletes selected inbox conversations", async () => {
    const user = userEvent.setup();
    const [first, second] = mockDb.tenants[0].conversations;
    let bulkUrl = "";
    let requestedIds: string[] = [];
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "POST" && request.url.includes("/conversations/bulk-delete")) {
        bulkUrl = request.url;
        void request.clone().json().then((body) => {
          requestedIds = (body as { ids: string[] }).ids;
        });
      }
    };
    server.events.on("request:start", listener);
    renderApp("/inbox", { loggedIn: true });
    const firstLink = await screen.findByRole("link", { name: first.summary ?? "No summary" });
    const secondLink = screen.getByRole("link", { name: second.summary ?? "No summary" });
    await user.click(within(firstLink.closest(".inbox-row") as HTMLElement).getByRole("checkbox"));
    await user.click(within(secondLink.closest(".inbox-row") as HTMLElement).getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Delete selected (2)" }));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(
      "Their transcripts and recordings are permanently deleted. Contacts are kept. This can't be undone.",
    )).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(requestedIds).toEqual([first.id, second.id]));
    expect(new URL(bulkUrl).pathname).toBe("/api/conversations/bulk-delete");
    await waitFor(() => {
      expect(screen.queryByRole("link", { name: first.summary ?? "No summary" })).not.toBeInTheDocument();
      expect(screen.queryByRole("link", { name: second.summary ?? "No summary" })).not.toBeInTheDocument();
    });
    server.events.removeListener("request:start", listener);
  });

  it("deletes a conversation from the selected detail header", async () => {
    const user = userEvent.setup();
    const conversation = mockDb.tenants[0].conversations[0];
    let deleteUrl = "";
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "DELETE" && request.url.includes("/conversations/")) {
        deleteUrl = request.url;
      }
    };
    server.events.on("request:start", listener);
    renderApp("/inbox", { loggedIn: true });
    const link = await screen.findByRole("link", { name: conversation.summary ?? "No summary" });
    await user.click(link);
    await screen.findByRole("region", { name: "Selected conversation" });
    await user.click(screen.getByRole("button", { name: "Delete conversation" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteUrl).toContain(`/api/conversations/${conversation.id}`));
    await waitFor(() => expect(
      screen.queryByRole("link", { name: conversation.summary ?? "No summary" }),
    ).not.toBeInTheDocument());
    server.events.removeListener("request:start", listener);
  });

  it("shows deletion controls only to owners", async () => {
    const previousRole = mockDb.memberships[0].role;
    mockDb.memberships[0].role = "staff";
    const inbox = renderApp("/inbox", { loggedIn: true });
    await screen.findByRole("link", { name: /Boiler not firing, no hot water/ });
    expect(screen.queryByRole("button", { name: /Delete selected/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete conversation" })).not.toBeInTheDocument();
    inbox.unmount();

    renderApp("/assistants", { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    mockDb.memberships[0].role = previousRole;
  });

  it("shows assistant conversation count and requests conversation deletion", async () => {
    const user = userEvent.setup();
    const assistant = mockDb.tenants[0].assistants[0];
    let deleteUrl = "";
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "DELETE" && request.url.includes(`/assistants/${assistant.id}`)) {
        deleteUrl = request.url;
      }
    };
    server.events.on("request:start", listener);
    renderApp("/assistants", { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: "Delete Brightside reception" }));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(
      "This also permanently deletes its 7 conversations, including transcripts and recordings. Contacts are kept. This can't be undone.",
    )).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteUrl).not.toBe(""));
    expect(new URL(deleteUrl).searchParams.get("delete_conversations")).toBe("true");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Conversation is still in progress",
    );
    server.events.removeListener("request:start", listener);
  });

  it("redirects to login and returns to the inbox filters after sign-in", async () => {
    const user = userEvent.setup();
    renderApp("/inbox?channel=phone");
    await screen.findByRole("heading", { name: "Sign in" });
    await user.type(screen.getByLabelText("Email address"), "demo@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await user.click(await screen.findByRole("button", { name: /Filters/ }));
    await waitFor(() => expect(screen.getByLabelText("Channel")).toHaveValue("phone"));
    expect(screen.getByTestId("current-location")).toHaveTextContent("/inbox?channel=phone");
  });

  it("shows an inline error for a rejected password", async () => {
    const user = userEvent.setup();
    renderApp("/login");
    await user.type(screen.getByLabelText("Email address"), "demo@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
  });
});

describe("conversation list formatting", () => {
  it("groups loaded conversations by local day", () => {
    const base = mockDb.tenants[0].conversations[0];
    const groups = groupConversationDays([
      { ...base, id: "today", started_at: "2026-03-10T12:00:00Z" },
      { ...base, id: "yesterday", started_at: "2026-03-09T12:00:00Z" },
      { ...base, id: "older", started_at: "2026-03-08T12:00:00Z" },
    ], new Date("2026-03-10T12:00:00Z"));
    expect(groups.map(({ label, items }) => [label, items.length])).toEqual([
      ["Today", 1],
      ["Yesterday", 1],
      ["8 March 2026", 1],
    ]);
  });

  it("formats completed phone and web durations as minutes and seconds", () => {
    expect(formatConversationDuration("phone", "2026-03-10T10:00:00Z", "2026-03-10T10:01:05Z")).toBe("1:05");
    expect(formatConversationDuration("web", "2026-03-10T10:00:00Z", "2026-03-10T10:00:08Z")).toBe("0:08");
    expect(formatConversationDuration("phone", "2026-03-10T10:00:00Z", "2026-03-10T10:00:00.900Z")).toBeNull();
    expect(formatConversationDuration("whatsapp", "2026-03-10T10:00:00Z", "2026-03-10T10:01:05Z")).toBeNull();
    expect(formatConversationDuration("phone", "2026-03-10T10:00:00Z", null)).toBeNull();
  });
});

describe("bulk deletion", () => {
  it("deletes selected contacts", async () => {
    const user = userEvent.setup();
    const [first, second] = mockDb.tenants[0].contacts;
    let requestedIds: string[] = [];
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "POST" && request.url.includes("/contacts/bulk-delete")) {
        void request.clone().json().then((body) => { requestedIds = (body as { ids: string[] }).ids; });
      }
    };
    server.events.on("request:start", listener);
    renderApp("/contacts", { loggedIn: true });
    await screen.findByRole("link", { name: first.display_name });
    expect(screen.queryByRole("button", { name: /Delete selected/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: `Select ${first.display_name}` }));
    await user.click(screen.getByRole("checkbox", { name: `Select ${second.display_name}` }));
    await user.click(screen.getByRole("button", { name: "Delete selected (2)" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Their conversations and tasks are kept, without a contact. This can't be undone.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(requestedIds).toEqual([first.id, second.id]));
    await waitFor(() => expect(screen.queryByRole("link", { name: first.display_name })).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /Delete selected/ })).not.toBeInTheDocument();
    server.events.removeListener("request:start", listener);
  });

  it("selects all tasks and deletes them", async () => {
    const user = userEvent.setup();
    const total = mockDb.tenants[0].tasks.length;
    expect(total).toBeGreaterThan(0);
    let requestedIds: string[] = [];
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "POST" && request.url.includes("/tasks/bulk-delete")) {
        void request.clone().json().then((body) => { requestedIds = (body as { ids: string[] }).ids; });
      }
    };
    server.events.on("request:start", listener);
    renderApp("/tasks", { loggedIn: true });
    const selectAll = await screen.findByRole("checkbox", { name: "Select all tasks" });
    await user.click(selectAll);
    await user.click(screen.getByRole("button", { name: `Delete selected (${total})` }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(requestedIds).toHaveLength(total));
    await screen.findByText("No tasks match these filters.");
    server.events.removeListener("request:start", listener);
  });
});

describe("services suggestions", () => {
  it("replaces services with the suggestion and opens the editor", async () => {
    const user = userEvent.setup();
    let capturedBody: Record<string, unknown> | undefined;
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "PUT" && request.url.includes("/assistants/")) {
        void request.clone().json().then((body) => { capturedBody = body as Record<string, unknown>; });
      }
    };
    server.events.on("request:start", listener);
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    expect(screen.queryByLabelText("Service label")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Suggest from prompt" }));
    await screen.findByText("Suggested from your prompt and knowledge. Review, then save.");
    expect(screen.getByLabelText("Service label")).toHaveValue("Table reservation");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(capturedBody).toBeDefined());
    expect(Object.keys((capturedBody?.config as { services: Record<string, unknown> }).services)).toEqual(["table_reservation"]);
    server.events.removeListener("request:start", listener);
  });
});
