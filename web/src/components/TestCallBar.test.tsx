import { http, HttpResponse } from "msw";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WebCallEvents, WebCallSessionFactory } from "../call/webCall";
import { webCallSocketUrl } from "../call/webCall";
import { server } from "../mocks/server";
import type { Schemas } from "../api/client";
import { mockDb } from "../mocks/db";
import { renderApp } from "../test/renderApp";

const assistantId = "20000000-0000-4000-8000-000000000001";
const bar = () => screen.getByRole("region", { name: "Test call" });
const secondAssistantId = "20000000-0000-4000-8000-000000000002";

function withTwoAssistants() {
  server.use(http.get("*/api/assistants", () => HttpResponse.json<Schemas["AssistantListResponse"]>({
    items: [...mockDb.tenants[0].assistants, { ...mockDb.tenants[1].assistants[0], name: "Second assistant" }],
  })));
}

function fakeFactory() {
  const calls: { url: string; events: WebCallEvents; muted: boolean[]; hungUp: boolean }[] = [];
  const factory: WebCallSessionFactory = (url, events) => {
    const record = { url, events, muted: [] as boolean[], hungUp: false };
    calls.push(record);
    return {
      async start() {},
      setMuted(muted) { record.muted.push(muted); },
      hangUp() { record.hungUp = true; events.onEnded(); },
    };
  };
  return { factory, calls };
}

describe("global test-call bar", () => {
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); });
  afterEach(() => { vi.useRealTimers(); });

  it("runs a call through connecting, in call (timer, mute) and ended with a conversation link", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const { factory, calls } = fakeFactory();
    server.use(http.get("*/api/conversations", ({ request }) => {
      const url = new URL(request.url);
      if (url.searchParams.get("channel") !== "web") return undefined;
      const item: Schemas["ConversationSummary"] = {
        id: "conv-test-1", assistant_id: assistantId, channel: "web", status: "completed", test_call: true, needs_review: false,
        person_id: null, contact_name: null, external_ref: "web:test", summary: "Test call", started_at: new Date().toISOString(), ended_at: null, end_reason: null,
      };
      return HttpResponse.json<Schemas["ConversationListResponse"]>({ items: [item], next_cursor: null });
    }));
    withTwoAssistants();
    renderApp("/inbox", { loggedIn: true, createWebCallSession: factory });
    await screen.findByRole("heading", { name: "Inbox" });

    const region = bar();
    const assistantSelect = await within(region).findByLabelText("Assistant to test");
    await user.selectOptions(assistantSelect, assistantId);
    expect(assistantSelect).toHaveValue(assistantId);
    const startCall = within(region).getByRole("button", { name: "Start test call" });
    expect(startCall).toHaveAttribute("title", "Use headphones");
    await user.click(startCall);

    expect(await within(region).findByText(/Connecting to/)).toBeInTheDocument();
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].url).toBe("http://127.0.0.1:7862/?call_token=mock-call-token");

    act(() => calls[0].events.onConnected());
    expect(within(region).getByText(/In call with/)).toBeInTheDocument();
    expect(within(region).getByText("Use headphones")).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(65_000); });
    expect(within(region).getByText("1:05")).toBeInTheDocument();

    const mute = within(region).getByRole("button", { name: "Mute" });
    await user.click(mute);
    expect(within(region).getByRole("button", { name: "Unmute" })).toHaveAttribute("aria-pressed", "true");
    expect(within(region).getByText("Muted")).toBeInTheDocument();
    expect(calls[0].muted).toEqual([true]);

    await user.click(within(region).getByRole("button", { name: "Hang up" }));
    expect(calls[0].hungUp).toBe(true);
    expect(within(region).getByText(/Call ended · 1:05/)).toBeInTheDocument();
    const link = await within(region).findByRole("link", { name: "View conversation" });
    expect(link).toHaveAttribute("href", "/conversations/conv-test-1");

    await user.click(within(region).getByRole("button", { name: "Dismiss" }));
    expect(within(region).getByRole("button", { name: "Start test call" })).toBeInTheDocument();
  });

  it("shows the API detail with a retry when the test call cannot be created", async () => {
    server.use(http.post("*/api/assistants/:id/test-calls", () => HttpResponse.json({ detail: "Test calls are not configured" }, { status: 503 })));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderApp("/inbox", { loggedIn: true, createWebCallSession: fakeFactory().factory });
    await screen.findByRole("heading", { name: "Inbox" });
    await user.click(within(bar()).getByRole("button", { name: "Start test call" }));

    expect(await within(bar()).findByRole("alert")).toHaveTextContent("Test calls are not configured");
    expect(within(bar()).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("reports microphone or connection errors from the call session", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const factory: WebCallSessionFactory = (_url, events) => ({
      async start() { events.onEnded("Microphone blocked. Allow the microphone for this site."); },
      setMuted() {}, hangUp() {},
    });
    renderApp("/inbox", { loggedIn: true, createWebCallSession: factory });
    await screen.findByRole("heading", { name: "Inbox" });
    await user.click(within(bar()).getByRole("button", { name: "Start test call" }));

    expect(await within(bar()).findByRole("alert")).toHaveTextContent("Microphone blocked");
    expect(within(bar()).getByRole("link", { name: "Open in new tab" })).toHaveAttribute("href", "http://127.0.0.1:7862/?call_token=mock-call-token");
  });

  it("is hidden for staff members who cannot run test calls", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderApp("/inbox", { loggedIn: true, createWebCallSession: fakeFactory().factory });
    await screen.findByRole("heading", { name: "Inbox" });
    expect(bar()).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Workspace"), "10000000-0000-4000-8000-000000000002");
    await waitFor(() => expect(screen.queryByRole("region", { name: "Test call" })).not.toBeInTheDocument());
  });

  it("is hidden on platform admin routes", async () => {
    renderApp("/admin", { loggedIn: true, createWebCallSession: fakeFactory().factory });
    await screen.findByRole("heading", { name: "Admin" });
    expect(screen.queryByRole("region", { name: "Test call" })).not.toBeInTheDocument();
  });

  it("collapses with the keyboard and remembers the collapsed state and assistant", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    withTwoAssistants();
    renderApp("/inbox", { loggedIn: true, createWebCallSession: fakeFactory().factory });
    await screen.findByRole("heading", { name: "Inbox" });
    const assistantSelect = await within(bar()).findByLabelText("Assistant to test");
    await user.selectOptions(assistantSelect, secondAssistantId);
    expect(assistantSelect).toHaveValue(secondAssistantId);
    const toggle = within(bar()).getByRole("button", { name: "Collapse test call bar" });
    toggle.focus();
    await user.keyboard("{Enter}");
    expect(within(bar()).getByRole("button", { name: "Expand test call bar" })).toHaveAttribute("aria-expanded", "false");
    expect(within(bar()).queryByRole("button", { name: "Start test call" })).not.toBeInTheDocument();
    expect(localStorage.getItem("inbound.testcall.collapsed")).toBe("1");
    expect(localStorage.getItem("inbound.testcall.assistant.10000000-0000-4000-8000-000000000001")).toBe(secondAssistantId);
  });

  it("starts a call from the global bar while on assistant settings", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const { factory, calls } = fakeFactory();
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true, createWebCallSession: factory });
    await screen.findByRole("heading", { name: "Brightside reception" });
    expect(screen.queryByRole("button", { name: "Test call" })).not.toBeInTheDocument();
    expect(within(bar()).getAllByRole("button", { name: "Start test call" })).toHaveLength(1);
    await user.click(within(bar()).getByRole("button", { name: "Start test call" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    act(() => calls[0].events.onConnected());
    expect(within(bar()).getByText(/In call with/)).toBeInTheDocument();
    expect(within(bar()).getByText("Brightside reception")).toBeInTheDocument();
    expect(within(bar()).getByRole("button", { name: "Hang up" })).toBeInTheDocument();
  });

  it("links channel management from assistant settings", async () => {
    renderApp(`/assistants/${assistantId}/settings/channels`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    expect(await screen.findByRole("link", { name: "Manage channels" })).toHaveAttribute(
      "href",
      `/channels?assistant=${assistantId}`,
    );
  });
});

describe("webCallSocketUrl", () => {
  it("derives the websocket URL from the hosted call page URL", () => {
    expect(webCallSocketUrl("http://127.0.0.1:7862/?call_token=abc")).toBe("ws://127.0.0.1:7862/ws?call_token=abc");
    expect(webCallSocketUrl("https://app.example.com/call/?call_token=abc")).toBe("wss://app.example.com/call/ws?call_token=abc");
  });
});
