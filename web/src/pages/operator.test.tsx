import { http, HttpResponse } from "msw";
import { configure, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Schemas } from "../api/client";
import { server } from "../mocks/server";
import { mockDb } from "../mocks/db";
import { formatDate } from "../lib/date";
import { renderApp } from "../test/renderApp";
import { formatConversationDuration, groupConversationDays } from "./OperatorPages";

const assistantId = "20000000-0000-4000-8000-000000000001";
configure({ asyncUtilTimeout: 6000 });
type TestUser = ReturnType<typeof userEvent.setup>;

async function selectPostAction(
  user: TestUser,
  title: string,
  pickerName: RegExp,
  addAnother = false,
) {
  const addFromPicker = async () => {
    await user.click(screen.getByRole("button", { name: pickerName }));
    const expandButton = screen.queryByRole("button", { name: `Expand ${title} settings` });
    if (expandButton) await user.click(expandButton);
  };
  const actionsSwitch = screen.getByRole("switch", { name: "After-conversation actions" }) as HTMLInputElement;
  const existingAction = screen.queryAllByRole("button", { name: `Expand ${title} settings` })[0];
  if (!actionsSwitch.checked) {
    await user.click(actionsSwitch);
    await addFromPicker();
  } else if (addAnother || !existingAction) {
    await user.click(screen.getByRole("button", { name: "Add another action" }));
    await addFromPicker();
  } else {
    await user.click(existingAction);
  }
}

function enableSheetsFeature() {
  server.use(
    http.get("*/api/assistants/:id/google-features", () => HttpResponse.json({
      features: ["sheets"],
      email: "sheets@example.test",
    })),
  );
}

const conversationId = "50000000-0000-4000-8000-000000000001";
const gmailConversationId = "50000000-0000-4000-8000-000000000007";
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
      "Transfer Failed",
    );
    expect(screen.getByRole("heading", { name: "Tasks" }).parentElement).toHaveTextContent(
      "Caller asked for a person.",
    );
  });

  it("saves the complete config across tabs and keeps validation errors on their tab", async () => {
    const user = userEvent.setup();
    const initialConfig = structuredClone(mockDb.tenants[0].assistants[0].config);
    let capturedBody: Record<string, unknown> | undefined;
    const putListener = ({ request }: { request: Request }) => {
      if (request.method === "PUT" && request.url.includes("/assistants/")) {
        void request.clone().json().then((body) => { capturedBody = body as Record<string, unknown>; });
      }
    };
    server.events.on("request:start", putListener);
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: "Edit services and intake fields" }));
    expect(screen.queryByLabelText("Field name")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Service key")).not.toBeInTheDocument();

    const choices = screen.getAllByLabelText("Choices")[0];
    await user.clear(choices);
    await user.type(choices, "a, b");
    const serviceLabel = screen.getAllByLabelText("Service label")[0];
    await user.clear(serviceLabel);
    await user.type(serviceLabel, "Boiler servicing");
    await user.click(screen.getByRole("link", { name: "Rules" }));
    await user.clear(screen.getByLabelText("Phrases (comma or newline separated)"));
    await user.type(screen.getByLabelText("Phrases (comma or newline separated)"), "gas leak, flooding");
    await user.click(screen.getByRole("link", { name: "Advanced" }));
    await user.click(screen.getByRole("radio", { name: /Read email addresses back letter by letter/ }));
    await user.click(screen.getByRole("radio", { name: /Read back every field marked critical/ }));
    expect(screen.getByRole("heading", { name: "Readback" })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /transfer call/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: /request callback/ }));
    const threshold = screen.getByRole("slider", { name: /confidence threshold/i });
    fireEvent.change(threshold, { target: { value: "0.8" } });

    await waitFor(() => expect(capturedBody).toBeDefined());
    expect(await screen.findByText("All changes saved")).toBeInTheDocument();
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
    const knowledgeTab = screen.getByRole("link", { name: "Knowledge" });
    await user.click(knowledgeTab);
    await waitFor(() => expect(within(screen.getByRole("region", { name: "Settings sections and save status" })).getByRole("status")).toHaveTextContent("Not saved: fix the highlighted fields"), { timeout: 6000 });
    expect(screen.getByTestId("current-location")).toHaveTextContent(`/assistants/${assistantId}/settings/knowledge`);
    expect(screen.getByRole("alert")).toHaveTextContent("Also fix: General");
    expect(knowledgeTab).toHaveFocus();
    expect(screen.getByRole("link", { name: /General.*1 to fix/ })).toBeInTheDocument();
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
    await waitFor(() => expect(capturedBody).toBeDefined());
    const services = (capturedBody?.config as { services: Record<string, { label: string; fields: Array<{ name: string; label: string }> }> }).services;
    expect(services.boiler_repair_2).toMatchObject({ label: "Boiler repair", fields: [{ name: "caller_name", label: "Caller name" }] });
    expect(await screen.findByText("All changes saved")).toBeInTheDocument();

    capturedBody = undefined;
    const savedLabels = screen.getAllByLabelText("Service label");
    await user.type(savedLabels[savedLabels.length - 1], " and servicing");
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
      http.get("*/api/assistants/:id/google-features", () => HttpResponse.json({
        features: ["calendar"],
        email: "calendar@example.test",
      })),
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
      http.get("*/api/assistants/:id/google-features", () => HttpResponse.json({
        features: ["calendar"],
        email: "calendar@example.test",
      })),
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
    await selectPostAction(user, "Email the customer", /^Email the customer Send the customer/);
    expect(document.querySelector('section.stack[aria-label="After-conversation actions"]:not(.ability-card)')).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { name: "After-conversation actions" })).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Send test" }));
    expect(await screen.findByText("Ready to send.")).toBeInTheDocument();
    expect(screen.getByText(/Thank you for contacting Brightside Plumbing/)).toBeInTheDocument();
  });

  it("offers Google Sheets connection when a sheet action has no Sheets scope", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: /After-conversation actions/ }));
    await selectPostAction(user, "Add a Google Sheets row", /^Add a Google Sheets row Add conversation details/, true);
    expect(await screen.findByRole("button", { name: "Connect Google Sheets" })).toBeInTheDocument();
  });

  it("adds multiple after-conversation actions and expands only the new action", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: /After-conversation actions/ }));
    await selectPostAction(user, "Email the customer", /^Email the customer Send the customer/);
    expect(screen.getByRole("button", { name: "Collapse Email the customer settings" })).toHaveAttribute("aria-expanded", "true");

    await user.click(screen.getByRole("button", { name: "Add another action" }));
    await user.click(screen.getByRole("button", { name: /Notify your team/ }));

    expect(screen.getByRole("button", { name: "Expand Email the customer settings" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: "Collapse Notify your team settings" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.queryByLabelText("Subject template 1")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /^Staff recipients/ })).toBeVisible();
  });

  it("inserts a selected variable into a subject at the cursor", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: /After-conversation actions/ }));
    await selectPostAction(user, "Email the customer", /^Email the customer Send the customer/);

    const subject = screen.getByLabelText("Subject template 1") as HTMLInputElement;
    await user.clear(subject);
    await user.type(subject, "Hello world");
    subject.setSelectionRange(6, 6);
    const templateInput = subject.closest(".template-input") as HTMLElement;
    await user.click(within(templateInput).getByRole("button", { name: "Insert variable" }));
    await user.click(within(templateInput).getByRole("button", { name: /Customer name/ }));

    await waitFor(() => {
      expect(subject).toHaveValue("Hello {{contact.name}}world");
      expect(subject).toHaveFocus();
    });
  });

  it("dismisses the variable popover on Escape and outside pointerdown", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: /After-conversation actions/ }));
    await selectPostAction(user, "Email the customer", /^Email the customer Send the customer/);

    const subject = screen.getByLabelText("Subject template 1");
    const templateInput = subject.closest(".template-input") as HTMLElement;
    const scopedInput = within(templateInput);
    const trigger = scopedInput.getByRole("button", { name: "Insert variable" });
    await user.click(trigger);
    expect(scopedInput.getByRole("button", { name: "Conversation summary" })).not.toHaveTextContent("Conversation summary.");
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(scopedInput.queryByRole("button", { name: /Customer name/ })).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
    });

    await user.click(trigger);
    fireEvent.pointerDown(document.body);
    await waitFor(() => expect(scopedInput.queryByRole("button", { name: /Customer name/ })).not.toBeInTheDocument());
  });

  it("inserts a slugged custom variable into a subject", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: /After-conversation actions/ }));
    await selectPostAction(user, "Email the customer", /^Email the customer Send the customer/);

    const subject = screen.getByLabelText("Subject template 1") as HTMLInputElement;
    await user.clear(subject);
    const templateInput = subject.closest(".template-input") as HTMLElement;
    await user.click(within(templateInput).getByRole("button", { name: "Insert variable" }));
    await user.click(within(templateInput).getByRole("button", { name: /Custom variable…/ }));
    await user.type(screen.getByLabelText("Custom variable name"), "Booking ref");
    const customForm = screen.getByLabelText("Custom variable name").closest("form") as HTMLFormElement;
    await user.click(within(customForm).getByRole("button", { name: "Insert variable" }));

    await waitFor(() => expect(subject).toHaveValue("{{booking_ref}}"));
  });

  it("stores a pasted Google Sheets link as its spreadsheet ID", async () => {
    const user = userEvent.setup();
    enableSheetsFeature();
    let capturedBody: Record<string, unknown> | undefined;
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "PUT" && request.url.includes("/assistants/")) {
        void request.clone().json().then((body) => { capturedBody = body as Record<string, unknown>; });
      }
    };
    server.events.on("request:start", listener);
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: /After-conversation actions/ }));
    await selectPostAction(user, "Add a Google Sheets row", /^Add a Google Sheets row Add conversation details/);

    expect(await screen.findByRole("button", { name: "Collapse Add a Google Sheets row settings" })).toHaveAttribute("aria-expanded", "true");
    const link = await screen.findByLabelText("Google Sheet link 1");
    const pastedLink = "https://docs.google.com/spreadsheets/u/0/d/sheet_123/edit#gid=0";
    await user.click(link);
    await user.paste(pastedLink);
    expect(link).toHaveValue(pastedLink);
    expect(screen.getByRole("link", { name: "Open sheet" })).toHaveAttribute("href", "https://docs.google.com/spreadsheets/d/sheet_123/edit");
    expect(screen.getByText("Sheet linked · tab Sheet1")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Tab name 1"));
    await user.type(screen.getByLabelText("Tab name 1"), "Leads");
    expect(screen.getByText("Sheet linked · tab Leads")).toBeInTheDocument();

    await waitFor(() => expect(capturedBody).toBeDefined());
    const savedConfig = capturedBody?.config as { post_actions: Array<{ spreadsheet_id: string }> };
    expect(savedConfig.post_actions[0]?.spreadsheet_id).toBe("sheet_123");
    server.events.removeListener("request:start", listener);
  }, 10000);

  it("shows invalid Sheets links without writing them into the config", async () => {
    const user = userEvent.setup();
    enableSheetsFeature();
    let capturedBody: Record<string, unknown> | undefined;
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "PUT" && request.url.includes("/assistants/")) {
        void request.clone().json().then((body) => { capturedBody = body as Record<string, unknown>; });
      }
    };
    server.events.on("request:start", listener);
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: /After-conversation actions/ }));
    await selectPostAction(user, "Add a Google Sheets row", /^Add a Google Sheets row Add conversation details/);

    await user.type(await screen.findByLabelText("Google Sheet link 1"), "https://docs.google.com/document/d/not-a-sheet/edit");
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a valid Google Sheets link or spreadsheet ID.");
    expect(screen.queryByRole("link", { name: "Open sheet" })).not.toBeInTheDocument();

    await waitFor(() => expect(capturedBody).toBeDefined());
    const savedConfig = capturedBody?.config as { post_actions: Array<{ spreadsheet_id: string }> };
    expect(savedConfig.post_actions[0]?.spreadsheet_id).toBe("");
    server.events.removeListener("request:start", listener);
  });

  it("starts new Sheets actions with the default columns", async () => {
    const user = userEvent.setup();
    enableSheetsFeature();
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: /After-conversation actions/ }));
    await selectPostAction(user, "Add a Google Sheets row", /^Add a Google Sheets row Add conversation details/, true);

    const defaults = [
      ["Started", "started_at"],
      ["Name", "contact.name"],
      ["Channel", "channel"],
      ["Summary", "summary"],
    ];
    defaults.forEach(([header, value], index) => {
      expect(screen.getByLabelText(`Column header 1-${index + 1}`)).toHaveValue(header);
      expect(screen.getByLabelText(`Column value 1-${index + 1}`)).toHaveValue(value);
    });
  });

  it("stores the selected variable in a Sheets column value", async () => {
    const user = userEvent.setup();
    enableSheetsFeature();
    let capturedBody: Record<string, unknown> | undefined;
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "PUT" && request.url.includes("/assistants/")) {
        void request.clone().json().then((body) => { capturedBody = body as Record<string, unknown>; });
      }
    };
    server.events.on("request:start", listener);
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: /After-conversation actions/ }));
    await selectPostAction(user, "Add a Google Sheets row", /^Add a Google Sheets row Add conversation details/);
    await user.type(screen.getByLabelText("Google Sheet link 1"), "sheet_123");
    const value = screen.getByLabelText("Column value 1-1");
    await user.selectOptions(value, "summary");
    await user.selectOptions(value, "contact.name");

    await waitFor(() => expect(capturedBody).toBeDefined());
    const savedConfig = capturedBody?.config as { post_actions: Array<{ columns: Array<{ value_template: string }> }> };
    expect(savedConfig.post_actions[0]?.columns[0]?.value_template).toBe("{{contact.name}}");
    server.events.removeListener("request:start", listener);
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
    expect(tenant.assistants).toHaveLength(3);
    expect(tenant.assistants[0].id).toBe(existingAssistantId);
    const createdAssistant = tenant.assistants.find(({ name }) => name === "Second assistant");
    expect(createdAssistant).toBeDefined();
    expect(tenant.assistantData[existingAssistantId].versions).toHaveLength(3);
    expect(tenant.assistantData[createdAssistant!.id].versions).toHaveLength(1);
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
    expect(transcript).not.toHaveAttribute("data-layout");
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

  it("renders Gmail history as a message thread with formatted timestamps", async () => {
    const conversation = structuredClone(mockDb.tenants[0].conversations.find(({ id }) => id === gmailConversationId)!);
    const customerMessage = conversation.messages.find(({ role }) => role !== "assistant")!;
    server.use(http.get<{ id: string }, never, Schemas["ConversationDetailResponse"]>(
      "*/api/conversations/:id",
      () => HttpResponse.json<Schemas["ConversationDetailResponse"]>(conversation),
    ));
    renderApp(`/conversations/${gmailConversationId}`, { loggedIn: true });

    const transcript = await screen.findByRole("list", { name: "Conversation transcript" });
    expect(transcript).toHaveAttribute("data-layout", "message");
    const customerRow = within(transcript).getByText("Customer").closest("li");
    expect(customerRow).not.toBeNull();
    if (!customerRow) throw new Error("Customer transcript row was not rendered.");
    expect(within(customerRow).getByText("Customer")).toBeInTheDocument();
    const time = customerRow.querySelector("time");
    expect(time).not.toBeNull();
    if (!time) throw new Error("Customer transcript timestamp was not rendered.");
    expect(time).toHaveAttribute("datetime", customerMessage.ts);
    expect(time).toHaveTextContent(formatDate(customerMessage.ts));
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
    const assistantFilter = screen.getByLabelText("Assistant");
    await within(assistantFilter).findByRole("option", { name: "Brightside reception" });
    await user.selectOptions(assistantFilter, assistantId);
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

  it("shows readable task labels and details without exposing known payload JSON", async () => {
    const tasks: Schemas["TaskListResponse"] = {
      items: [
        {
          id: "60000000-0000-4000-8000-000000000101",
          kind: "action_setup_needed",
          status: "open",
          due_at: null,
          source: "live",
          payload: {
            action_key: "email_customer",
            message: "Connect a Gmail account to send follow-up emails",
          },
        },
        {
          id: "60000000-0000-4000-8000-000000000102",
          kind: "follow_up",
          status: "open",
          due_at: null,
          source: "post_call",
          payload: {
            title: "Confirm availability",
            reason: "The caller asked about Monday or Tuesday.",
          },
        },
        {
          id: "60000000-0000-4000-8000-000000000103",
          kind: "callback",
          status: "open",
          due_at: null,
          source: "post_call",
          payload: { reason: "The caller asked for a call back." },
        },
        {
          id: "60000000-0000-4000-8000-000000000104",
          kind: "follow_up",
          status: "open",
          due_at: null,
          source: "post_call",
          payload: {
            reason: "Staff should confirm the next available time.",
            message: "Check availability.",
          },
        },
        {
          id: "60000000-0000-4000-8000-000000000105",
          kind: "urgent",
          status: "open",
          due_at: null,
          source: "post_call",
          payload: { reason: "A time-sensitive issue needs review." },
        },
        {
          id: "60000000-0000-4000-8000-000000000106",
          kind: "action_failed",
          status: "open",
          due_at: null,
          source: "post_call",
          payload: { message: "The email could not be sent." },
        },
        {
          id: "60000000-0000-4000-8000-000000000107",
          kind: "custom_task",
          status: "open",
          due_at: null,
          source: "live",
          payload: { opaque: "preserved for unknown kinds" },
        },
      ],
    };
    server.use(
      http.get<never, never, Schemas["TaskListResponse"]>(
        "*/api/tasks",
        () => HttpResponse.json(tasks),
      ),
    );

    renderApp("/tasks", { loggedIn: true });
    await screen.findByRole("columnheader", { name: "Task" });

    await screen.findByRole("cell", { name: "Setup needed" });
    expect(screen.getByRole("cell", { name: "Setup needed" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Confirm availability" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Call back" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Follow up" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Urgent follow-up" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Action failed" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Custom Task" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "The caller asked about Monday or Tuesday." })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Connect a Gmail account to send follow-up emails" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Staff should confirm the next available time." })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: '{"opaque":"preserved for unknown kinds"}' })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Select Call back task" })).toBeInTheDocument();
    expect(screen.getByLabelText("Status for Confirm availability")).toBeInTheDocument();
    expect(screen.queryByText('{"action_key":"email_customer","message":"Connect a Gmail account to send follow-up emails"}')).not.toBeInTheDocument();
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
    const assistantFilter = screen.getByLabelText("Assistant");
    await within(assistantFilter).findByRole("option", { name: "Brightside reception" });
    await user.selectOptions(assistantFilter, assistantId);
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
    await waitFor(() => expect(capturedBody).toBeDefined());
    expect(Object.keys((capturedBody?.config as { services: Record<string, unknown> }).services)).toEqual(["table_reservation"]);
    server.events.removeListener("request:start", listener);
  });
});
