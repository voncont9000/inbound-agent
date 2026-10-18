import { http, HttpResponse } from "msw";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { mockDashboardSummary } from "../mocks/dashboard";
import { server } from "../mocks/server";
import { renderApp } from "../test/renderApp";

const assistantId = "20000000-0000-4000-8000-000000000001";

function captureDashboardRequests() {
  const requests: URLSearchParams[] = [];
  server.events.on("request:start", ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname.endsWith("/api/dashboard/summary")) requests.push(url.searchParams);
  });
  return requests;
}

afterEach(() => server.events.removeAllListeners());

const card = (name: string) => screen.getByRole("region", { name });

describe("home dashboard", () => {
  it("shows stat cards and the chart for the last 7 days by default", async () => {
    const requests = captureDashboardRequests();
    renderApp("/", { loggedIn: true });
    expect(await screen.findByRole("heading", { name: "Home" })).toBeInTheDocument();
    const expected = mockDashboardSummary({ range: "7d" }, 1);

    await waitFor(() => expect(within(card("Conversations")).getByText(String(expected.conversations))).toBeInTheDocument());
    expect(within(card("Conversations")).getByText(`Phone ${expected.by_channel.phone} · WhatsApp ${expected.by_channel.whatsapp} · Gmail ${expected.by_channel.gmail}`)).toBeInTheDocument();
    expect(within(card("Handled by assistant")).getByText(String(expected.handled_by_assistant))).toBeInTheDocument();
    expect(within(card("Handled by assistant")).getByText(new RegExp(`${expected.passed_to_staff} passed to staff`))).toBeInTheDocument();
    expect(within(card("Bookings made")).getByText(String(expected.bookings))).toBeInTheDocument();
    expect(within(card("Callbacks & tasks")).getByText(String(expected.tasks))).toBeInTheDocument();
    expect(within(card("Median response time")).getByText(`${expected.response_latency.median_seconds!.toFixed(1)} s`)).toBeInTheDocument();
    expect(within(card("AI & telephony cost")).getByText(/^AI £|^AI US\$|^AI \$/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: `Conversations over time: ${expected.conversations} in 7 days` })).toBeInTheDocument();
    expect(screen.getAllByTestId("chart-bar")).toHaveLength(7);
    expect(screen.getByRole("link", { name: "Home" })).toHaveClass("active");
    expect(requests[0].get("range")).toBe("7d");
    expect(requests[0].get("timezone")).toBeTruthy();
    expect(requests[0].has("assistant_id")).toBe(false);
  });

  it("changes the range and assistant filter", async () => {
    const requests = captureDashboardRequests();
    const user = userEvent.setup();
    renderApp("/", { loggedIn: true });
    await screen.findByRole("img", { name: /in 7 days/ });

    await user.selectOptions(screen.getByLabelText("Time range"), "today");
    expect(await screen.findByRole("img", { name: /in 24 hours/ })).toBeInTheDocument();
    expect(screen.getByTestId("current-location")).toHaveTextContent("/?range=today");

    await user.selectOptions(await screen.findByRole("combobox", { name: "Assistant" }), await screen.findByRole("option", { name: "Brightside reception" }));
    await waitFor(() => expect(requests.at(-1)?.get("assistant_id")).toBe(assistantId));
    expect(requests.at(-1)?.get("range")).toBe("today");

    await user.selectOptions(screen.getByLabelText("Time range"), "30d");
    expect(await screen.findByRole("img", { name: /in 30 days/ })).toBeInTheDocument();
    expect(screen.getByTestId("current-location")).toHaveTextContent(`/?range=30d&assistant=${assistantId}`);
  });

  it("sends custom date ranges and validates their order", async () => {
    const requests = captureDashboardRequests();
    const user = userEvent.setup();
    renderApp("/?range=custom&start=2026-03-01&end=2026-03-03", { loggedIn: true });
    expect(await screen.findByRole("img", { name: /in 3 days/ })).toBeInTheDocument();
    expect(requests.at(-1)?.get("start")).toBe("2026-03-01");
    expect(requests.at(-1)?.get("end")).toBe("2026-03-03");

    const startInput = screen.getByLabelText("From");
    await user.clear(startInput);
    await user.type(startInput, "2026-03-09");
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a start date on or before the end date.");
  });

  it("shows an empty state when there are no conversations", async () => {
    server.use(http.get("*/api/dashboard/summary", () => HttpResponse.json(mockDashboardSummary({ range: "7d" }, 1, 0))));
    renderApp("/", { loggedIn: true });
    expect(await screen.findByText("No conversations in this period yet. Practice calls are not counted.")).toBeInTheDocument();
    expect(within(card("Conversations")).getByText("0")).toBeInTheDocument();
    expect(within(card("Median response time")).getByText("—")).toBeInTheDocument();
    expect(within(card("Median response time")).getByText("No measured calls")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /Conversations over time/ })).not.toBeInTheDocument();
  });

  it("shows an error when the summary cannot be loaded", async () => {
    server.use(http.get("*/api/dashboard/summary", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    renderApp("/", { loggedIn: true });
    expect(await screen.findByRole("alert")).toHaveTextContent("The overview could not be loaded.");
  });

  it("keeps the inbox reachable from the navigation", async () => {
    const user = userEvent.setup();
    renderApp("/", { loggedIn: true });
    await screen.findByRole("heading", { name: "Home" });
    await user.click(screen.getByRole("link", { name: "Inbox" }));
    expect(await screen.findByRole("heading", { name: "Inbox" })).toBeInTheDocument();
    expect(screen.getByTestId("current-location")).toHaveTextContent("/inbox");
  });
});
