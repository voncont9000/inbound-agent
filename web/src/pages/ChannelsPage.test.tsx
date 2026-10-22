import { waitFor, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { server } from "../mocks/server";
import { mockDb } from "../mocks/db";
import { renderApp } from "../test/renderApp";

const firstAssistantId = "20000000-0000-4000-8000-000000000001";
const secondAssistantId = "20000000-0000-4000-8000-000000000003";
const gmailAddress = "assistant@example.test";
const phoneNumber = "+442079460001";

describe("workspace Channels page", () => {
  it("lists connections across assistants and filters by kind and search", async () => {
    const user = userEvent.setup();
    renderApp("/channels", { loggedIn: true });

    expect(await screen.findByText(gmailAddress)).toBeInTheDocument();
    expect(screen.getByText(phoneNumber)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Channels" })).toHaveAttribute("href", "/channels");
    expect(screen.getByLabelText(`Assistant for ${gmailAddress}`)).toHaveValue(firstAssistantId);
    expect(screen.getByLabelText(`Assistant for ${phoneNumber}`)).toHaveValue(secondAssistantId);

    await user.click(screen.getByRole("button", { name: "Phone" }));
    expect(screen.queryByText(gmailAddress)).not.toBeInTheDocument();
    expect(screen.getByText(phoneNumber)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "All" }));

    const search = screen.getByRole("searchbox", { name: "Search channels" });
    await user.type(search, "Harbor");
    expect(screen.queryByText(gmailAddress)).not.toBeInTheDocument();
    expect(screen.getByText(phoneNumber)).toBeInTheDocument();
    await user.clear(search);
    await user.type(search, "assistant@example");
    expect(screen.getByText(gmailAddress)).toBeInTheDocument();
    expect(screen.queryByText(phoneNumber)).not.toBeInTheDocument();
  });

  it("shows an unassigned channel and reassigns it through the workspace endpoint", async () => {
    const user = userEvent.setup();
    const original = mockDb.channels[firstAssistantId].find(({ external_id }) => external_id === gmailAddress)!;
    mockDb.channels[firstAssistantId] = mockDb.channels[firstAssistantId].filter(
      ({ id }) => id !== original.id,
    );
    mockDb.unassignedChannels[mockDb.tenants[0].tenant.id].push({
      ...original,
      assistant_id: null,
    });
    let capturedBody: unknown;
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "PATCH" && new URL(request.url).pathname.endsWith(`/api/channels/${original.id}`)) {
        void request.clone().json().then((body) => { capturedBody = body; });
      }
    };
    server.events.on("request:start", listener);
    renderApp("/channels", { loggedIn: true });

    expect(await screen.findByText("Unassigned")).toBeInTheDocument();
    const select = screen.getByLabelText(`Assistant for ${gmailAddress}`);
    expect(select).toHaveValue("");
    expect(screen.getByRole("option", { name: "Unassigned — choose an assistant" })).toBeDisabled();
    await user.selectOptions(select, secondAssistantId);
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Move inbox" }));

    await waitFor(() => expect(capturedBody).toEqual({ assistant_id: secondAssistantId }));
    expect(select).toHaveValue(secondAssistantId);
    server.events.removeListener("request:start", listener);
  });

  it("adds a phone channel for a selected second assistant", async () => {
    const user = userEvent.setup();
    let capturedBody: unknown;
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "POST" && new URL(request.url).pathname.endsWith("/api/channels")) {
        void request.clone().json().then((body) => { capturedBody = body; });
      }
    };
    server.events.on("request:start", listener);
    renderApp("/channels", { loggedIn: true });
    await screen.findByText(gmailAddress);
    await user.click(screen.getByRole("button", { name: "Add channel" }));
    await user.click(screen.getByRole("button", { name: /Phone number/ }));

    const form = screen.getByRole("form", { name: "Add phone channel" });
    await user.selectOptions(within(form).getByLabelText("Assistant"), secondAssistantId);
    await user.type(within(form).getByLabelText("Phone number"), "+447700900123");
    await user.click(within(form).getByRole("button", { name: "Add channel" }));

    await waitFor(() => expect(capturedBody).toEqual({
      kind: "phone",
      external_id: "+447700900123",
      assistant_id: secondAssistantId,
      settings: {},
    }));
    expect(await screen.findByText("+447700900123")).toBeInTheDocument();
    server.events.removeListener("request:start", listener);
  });

  it("confirms Gmail reassignment before patching the assistant", async () => {
    const user = userEvent.setup();
    let capturedBody: unknown;
    const gmailId = "40000000-0000-4000-8000-000000000001";
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "PATCH" && new URL(request.url).pathname.endsWith(`/api/channels/${gmailId}`)) {
        void request.clone().json().then((body) => { capturedBody = body; });
      }
    };
    server.events.on("request:start", listener);
    renderApp("/channels", { loggedIn: true });
    await screen.findByText(gmailAddress);
    await user.selectOptions(screen.getByLabelText(`Assistant for ${gmailAddress}`), secondAssistantId);

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent(
      "Calendar booking and Google Sheets actions move with this inbox to Harbor assistant.",
    );
    expect(capturedBody).toBeUndefined();
    await user.click(within(dialog).getByRole("button", { name: "Move inbox" }));

    await waitFor(() => expect(capturedBody).toEqual({ assistant_id: secondAssistantId }));
    expect(screen.getByLabelText(`Assistant for ${gmailAddress}`)).toHaveValue(secondAssistantId);
    server.events.removeListener("request:start", listener);
  });

  it("removes a channel after confirmation", async () => {
    const user = userEvent.setup();
    renderApp("/channels", { loggedIn: true });
    await screen.findByText(gmailAddress);
    await user.click(screen.getByRole("button", { name: `More options for ${gmailAddress}` }));
    await user.click(screen.getByRole("menuitem", { name: "Remove" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Google Calendar and Sheets");
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(screen.queryByText(gmailAddress)).not.toBeInTheDocument());
  });

  it("saves Gmail include filters through the workspace endpoint", async () => {
    const user = userEvent.setup();
    let capturedBody: unknown;
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "PATCH" && new URL(request.url).pathname.startsWith("/api/channels/")) {
        void request.clone().json().then((body) => { capturedBody = body; });
      }
    };
    server.events.on("request:start", listener);
    renderApp("/channels", { loggedIn: true });
    await screen.findByText(gmailAddress);
    await user.click(screen.getByRole("button", { name: `More options for ${gmailAddress}` }));
    await user.click(screen.getByRole("menuitem", { name: "Email filters" }));
    await user.click(screen.getByRole("button", { name: "Add filter" }));
    await user.type(screen.getByLabelText("Contains filter 1"), "@vinci-labs.tech");
    await user.click(screen.getByRole("button", { name: "Save filters" }));

    await waitFor(() => expect(capturedBody).toEqual({
      include_rules: [{ field: "to", contains: "@vinci-labs.tech" }],
    }));
    expect(await screen.findByText("Filters saved.")).toBeInTheDocument();
    server.events.removeListener("request:start", listener);
  });

  it("closes the row menu on Escape", async () => {
    const user = userEvent.setup();
    renderApp("/channels", { loggedIn: true });
    await screen.findByText(gmailAddress);
    const trigger = screen.getByRole("button", { name: `More options for ${gmailAddress}` });
    await user.click(trigger);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("filters by the assistant query parameter and removes the filter chip", async () => {
    const user = userEvent.setup();
    renderApp(`/channels?assistant=${secondAssistantId}`, { loggedIn: true });
    expect(await screen.findByText(phoneNumber)).toBeInTheDocument();
    expect(screen.queryByText(gmailAddress)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Assistant: Harbor assistant/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Remove assistant filter/ }));
    expect(await screen.findByText(gmailAddress)).toBeInTheDocument();
  });
});
