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
const contactId = "30000000-0000-4000-8000-000000000001";

describe("operator workflows", () => {
  it("validates settings, saves the complete config, and shows server validation paths", async () => {
    const user = userEvent.setup();
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
    await user.type(enumChoices, "no_heating, leak");
    await user.clear(fieldNames[1]);
    await user.type(fieldNames[1], "phone");
    await user.click(screen.getByRole("radio", { name: /Read email addresses back letter by letter/ }));
    await user.click(screen.getByRole("radio", { name: /Read back every field marked critical/ }));
    await user.click(screen.getByRole("checkbox", { name: /request callback/ }));
    const threshold = screen.getByRole("slider", { name: /confidence threshold/i });
    fireEvent.change(threshold, { target: { value: "0.8" } });

    server.use(http.put<{ id: string }, Schemas["AssistantUpdate"], Schemas["AssistantResponse"]>("*/api/assistants/:id", async ({ request }) => {
      const body = await request.json() as Schemas["AssistantUpdate"];
      const assistant = mockDb.tenants[0].assistant;
      return HttpResponse.json<Schemas["AssistantResponse"]>({
        ...assistant,
        ...body,
        name: body.name ?? assistant.name,
        template: body.template ?? assistant.template,
        version: 4,
        updated_at: new Date().toISOString(),
      });
    }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(capturedBody).toBeDefined());
    expect(await screen.findByText("Saved version 4")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled());
    const savedConfig = capturedBody?.config as Record<string, unknown>;
    expect(savedConfig.readback).toBe("critical");
    expect(savedConfig.live_actions).toContain("request_callback");
    expect(savedConfig.review_confidence_threshold).toBe(0.8);
    expect(savedConfig.knowledge).toEqual(mockDb.tenants[0].assistant.config.knowledge);
    expect(savedConfig.price_list).toEqual(mockDb.tenants[0].assistant.config.price_list);
    for (const service of Object.values(savedConfig.services as Record<string, { fields: Array<Record<string, unknown>> }>)) {
      for (const field of service.fields) {
        if (field.type !== "enum") expect(field).not.toHaveProperty("choices");
      }
    }

    server.use(http.put<{ id: string }, Schemas["AssistantUpdate"], Schemas["HTTPValidationError"]>("*/api/assistants/:id", () => HttpResponse.json<Schemas["HTTPValidationError"]>({
      detail: [{ loc: ["body", "config", "services", "boiler_repair", "fields", 0, "label"], msg: "Label rejected by server.", type: "value_error" }],
    }, { status: 422 })));
    const firstLabel = screen.getAllByLabelText("Field label")[0];
    await user.clear(firstLabel);
    await user.type(firstLabel, "Caller name");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(screen.getAllByText("Label rejected by server.")).toHaveLength(2));
    expect(firstLabel).toHaveAttribute("aria-invalid", "true");
    expect(document.getElementById(firstLabel.getAttribute("aria-describedby") ?? "")).toHaveTextContent("Label rejected by server.");
    server.events.removeListener("request:start", putListener);
  });

  it("resolves a review conflict only after explicit caller-confirmation override", async () => {
    const user = userEvent.setup();
    let reviewBody: unknown;
    const listener = ({ request }: { request: Request }) => {
      if (request.url.includes("/reviews")) void request.clone().json().then((body) => { reviewBody = body; });
    };
    server.events.on("request:start", listener);
    renderApp(`/conversations/${conversationId}`, { loggedIn: true });
    await screen.findByRole("heading", { name: /Caller asked for help/ });
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.getByText("Post-call")).toBeInTheDocument();
    expect(screen.getByText("Merged")).toBeInTheDocument();
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
    server.events.removeListener("request:start", listener);
  });

  it("shows merge confirmation and sends no merge request until confirmed", async () => {
    const user = userEvent.setup();
    let mergeBody: unknown;
    let mergeCount = 0;
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
    expect(screen.getByTestId("current-location")).toHaveTextContent(`/contacts/${contactId}`);
    expect(screen.getByRole("heading", { name: "Jamie Morgan" })).toBeInTheDocument();
  });

  it("confirms a split, sends selected identities, and navigates to the returned contact", async () => {
    const user = userEvent.setup();
    const splitId = "30000000-0000-4000-8000-000000000099";
    const contact = mockDb.tenants[0].contacts[0];
    const identityId = contact.identities[0].id;
    let splitBody: unknown;
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
