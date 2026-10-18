import { http, HttpResponse } from "msw";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Schemas } from "../api/client";
import { server } from "../mocks/server";
import { mockDb } from "../mocks/db";
import { renderApp } from "../test/renderApp";

const assistantId = "20000000-0000-4000-8000-000000000001";
const conversationId = "50000000-0000-4000-8000-000000000001";
const transferConversationId = "50000000-0000-4000-8000-000000000011";
const contactId = "30000000-0000-4000-8000-000000000001";

describe("operator workflows", () => {
  it("adds and saves a transfer target with the assistant configuration", async () => {
    const user = userEvent.setup();
    let capturedBody: Record<string, unknown> | undefined;
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "PUT" && request.url.includes("/assistants/")) {
        void request.clone().json().then((body) => { capturedBody = body as Record<string, unknown>; });
      }
    };
    server.events.on("request:start", listener);
    renderApp(`/assistants/${assistantId}/settings`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: "Add transfer target" }));
    await user.type(screen.getByLabelText("key"), "duty_solicitor");
    await user.type(screen.getByLabelText("label"), "Duty solicitor");
    const phone = screen.getByLabelText("phone");
    expect(phone).toHaveAttribute("type", "tel");
    await user.type(phone, "+447700900123");
    await user.type(screen.getByLabelText("When to use"), "The caller asks for a person.");
    await user.click(screen.getByRole("checkbox", { name: /transfer call/i }));
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

  it("shows transfer metrics and failed transfer task on conversation details", async () => {
    const transferConversation = mockDb.tenants[1].conversations[0];
    server.use(
      http.get<{ id: string }, never, Schemas["ConversationDetailResponse"]>(
        "*/api/conversations/:id",
        ({ params }) => HttpResponse.json({ ...transferConversation, id: params.id }),
      ),
    );
    renderApp(`/conversations/${transferConversationId}`, { loggedIn: true });
    await screen.findByRole("heading", { name: /Enquiry about unfair dismissal/ });

    const transfer = screen.getByRole("heading", { name: "Transfer" }).parentElement;
    expect(transfer).toHaveTextContent("duty_solicitor");
    expect(transfer).toHaveTextContent("no-answer");
    expect(transfer).toHaveTextContent("1200 ms");
    expect(screen.getByRole("heading", { name: "Tasks" }).parentElement).toHaveTextContent(
      "transfer_failed",
    );
  });

  it("validates settings, saves the complete config, and shows server validation paths", async () => {
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
    renderApp(`/assistants/${assistantId}/settings`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });

    const enumChoices = screen.getAllByLabelText("Choices")[0];
    const fieldNames = screen.getAllByLabelText("Field name");
    await user.clear(enumChoices);
    await user.clear(fieldNames[1]);
    await user.type(fieldNames[1], "caller_name");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("heading", { name: /errors to fix/ })).toBeInTheDocument();
    expect(enumChoices).toHaveAttribute("aria-invalid", "true");
    expect(fieldNames[1]).toHaveAttribute("aria-invalid", "true");
    expect(putCount).toBe(0);

    await user.clear(enumChoices);
    await user.type(enumChoices, "a, b");
    await user.clear(fieldNames[1]);
    await user.type(fieldNames[1], "phone");
    await user.clear(screen.getByLabelText("Phrases (comma or newline separated)"));
    await user.type(screen.getByLabelText("Phrases (comma or newline separated)"), "gas leak, flooding");
    fireEvent.change(fieldNames[6], { target: { value: "requested_visit_date" } });
    fireEvent.change(screen.getAllByLabelText("Service key")[0], { target: { value: "boiler_service" } });
    await user.click(screen.getByRole("radio", { name: /Read email addresses back letter by letter/ }));
    await user.click(screen.getByRole("radio", { name: /Read back every field marked critical/ }));
    await user.click(screen.getByRole("checkbox", { name: /request callback/ }));
    const threshold = screen.getByRole("slider", { name: /confidence threshold/i });
    fireEvent.change(threshold, { target: { value: "0.8" } });

    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(capturedBody).toBeDefined());
    expect(await screen.findByText("Saved version 4")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled());
    const savedConfig = capturedBody?.config as Record<string, unknown>;
    expect(savedConfig.readback).toBe("critical");
    expect(savedConfig.live_actions).toContain("request_callback");
    expect(savedConfig.review_confidence_threshold).toBe(0.8);
    expect(savedConfig.knowledge).toEqual(initialConfig.knowledge);
    expect(savedConfig.price_list).toEqual(initialConfig.price_list);
    const savedServices = savedConfig.services as Record<string, { fields: Array<Record<string, unknown>> }>;
    expect(savedServices.boiler_service.fields[4].choices).toEqual(["a", "b"]);
    expect((savedConfig.urgency_rules as Array<Record<string, unknown>>).find(({ type }) => type === "keyword")?.phrases).toEqual(["gas leak", "flooding"]);
    expect((savedConfig.urgency_rules as Array<Record<string, unknown>>).find(({ type }) => type === "date_deadline")).toMatchObject({ service: "boiler_service", field: "requested_visit_date" });
    for (const service of Object.values(savedServices)) {
      for (const field of service.fields) {
        if (field.type !== "enum") expect(field).not.toHaveProperty("choices");
      }
    }

    server.use(http.put<{ id: string }, Schemas["AssistantUpdate"], Schemas["HTTPValidationError"]>("*/api/assistants/:id", () => HttpResponse.json<Schemas["HTTPValidationError"]>({
      detail: [{ loc: ["body", "config", "services", "boiler_service", "fields", 0, "label"], msg: "Label rejected by server.", type: "value_error" }],
    }, { status: 422 })));
    const firstLabel = screen.getAllByLabelText("Field label")[0];
    await user.clear(firstLabel);
    await user.type(firstLabel, "Caller name");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(screen.getAllByText("Label rejected by server.")).toHaveLength(2));
    expect(firstLabel).toHaveAttribute("aria-invalid", "true");
    expect(document.getElementById(firstLabel.getAttribute("aria-describedby") ?? "")).toHaveTextContent("Label rejected by server.");
    server.events.removeListener("request:start", putListener);
  }, 15000);

  it("configures an appointment type and saves booking settings", async () => {
    const user = userEvent.setup();
    let savedConfig: Record<string, unknown> | undefined;
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "PUT" && request.url.includes("/assistants/")) {
        void request.clone().json().then((body) => { savedConfig = (body as { config: Record<string, unknown> }).config; });
      }
    };
    server.events.on("request:start", listener);
    renderApp(`/assistants/${assistantId}/settings`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("checkbox", { name: /enable booking configuration/i }));
    const duration = screen.getByLabelText("Duration (minutes)");
    await user.clear(duration);
    await user.type(duration, "45");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(savedConfig).toBeDefined());

    expect(savedConfig?.booking).toMatchObject({
      calendar_id: "primary",
      appointment_types: [{ key: "consultation", duration_minutes: 45 }],
    });
    server.events.removeListener("request:start", listener);
  });

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
    renderApp(`/assistants/${assistantId}/settings`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("checkbox", { name: /enable booking configuration/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/calendar is disconnected/i);
    await user.click(screen.getByRole("button", { name: "Connect Calendar" }));
    await waitFor(() => expect(oauthFeatures).toEqual(["calendar"]));
  });

  it("tests an after-conversation action and shows the rendered result", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.selectOptions(screen.getByLabelText("Add action"), "email_customer");
    await user.click(screen.getByRole("button", { name: "Send test" }));
    expect(await screen.findByText("Ready to send.")).toBeInTheDocument();
    expect(screen.getByText(/Thanks for contacting Brightside Plumbing/)).toBeInTheDocument();
  });

  it("offers Google Sheets connection when a sheet action has no Sheets scope", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.selectOptions(screen.getByLabelText("Add action"), "sheets_row");
    expect(await screen.findByRole("button", { name: "Connect Google Sheets" })).toBeInTheDocument();
  });

  it("rejects duplicate contact keys without overwriting a contact", async () => {
    const user = userEvent.setup();
    const contacts = mockDb.tenants[0].assistants[0].config.contacts;
    const keys = Object.keys(contacts);
    const firstKey = keys[0]!;
    const secondKey = keys[1]!;
    let putCount = 0;
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "PUT" && request.url.includes("/assistants/")) putCount += 1;
    };
    server.events.on("request:start", listener);
    renderApp(`/assistants/${assistantId}/settings`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });

    const firstContactKey = screen.getAllByLabelText("Contact key")[0];
    fireEvent.change(firstContactKey, { target: { value: secondKey } });
    expect(firstContactKey).toHaveValue(firstKey);
    expect(firstContactKey).toHaveAttribute("aria-invalid", "true");
    expect(screen.getAllByText("Contact keys must be unique.")).toHaveLength(2);
    expect(screen.getAllByLabelText("Contact value")[0]).toHaveValue(contacts[firstKey]);
    expect(screen.getAllByLabelText("Contact value")[1]).toHaveValue(contacts[secondKey]);

    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(putCount).toBe(0);
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
    await screen.findByRole("heading", { name: /Boiler not firing, no hot water/ });
    expect(screen.getByText("Test call")).toBeInTheDocument();
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

  it("updates inbox URL filters and sends channel plus review filters", async () => {
    const user = userEvent.setup();
    const requested: URL[] = [];
    const listener = ({ request }: { request: Request }) => {
      if (request.url.includes("/conversations?")) requested.push(new URL(request.url));
    };
    server.events.on("request:start", listener);
    renderApp("/inbox", { loggedIn: true });
    await screen.findByRole("heading", { name: "Inbox" });
    await user.selectOptions(screen.getByLabelText("Channel"), "phone");
    await user.click(screen.getByLabelText("Needs review only"));
    await waitFor(() => expect(requested.some((url) => url.searchParams.get("channel") === "phone" && url.searchParams.get("has_review") === "true")).toBe(true));
    await waitFor(() => expect(screen.getByTestId("current-location")).toHaveTextContent("/inbox?channel=phone&has_review=true"));
    await waitFor(() => expect(within(screen.getByRole("table")).getAllByRole("row").slice(1)).toHaveLength(1));
    expect(screen.getAllByText("Needs review").length).toBeGreaterThan(0);
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("phone");
    expect(rows[0]).toHaveTextContent("Jamie Morgan");
    server.events.removeListener("request:start", listener);
  });

  it("marks test calls in the inbox", async () => {
    renderApp("/inbox", { loggedIn: true });
    await screen.findByRole("heading", { name: "Inbox" });
    expect(await screen.findAllByText("Test call")).not.toHaveLength(0);
  });

  it("marks Gmail assistant messages as drafts", async () => {
    const conversation = structuredClone(mockDb.tenants[0].conversations[0]);
    conversation.channel = "gmail";
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

  it("redirects to login and returns to the inbox filters after sign-in", async () => {
    const user = userEvent.setup();
    renderApp("/inbox?channel=phone");
    await screen.findByRole("heading", { name: "Sign in" });
    await user.type(screen.getByLabelText("Email address"), "demo@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
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
