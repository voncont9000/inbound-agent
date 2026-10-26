import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { adminConversationDetails } from "../mocks/admin";
import { server } from "../mocks/server";
import { setMockPlatformAdmin } from "../mocks/db";
import { renderApp } from "../test/renderApp";
import { scoreTone } from "../lib/admin";

function captureAdminRequests(path: string) {
  const requests: URLSearchParams[] = [];
  server.events.on("request:start", ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname.endsWith(path)) requests.push(url.searchParams);
  });
  return requests;
}

afterEach(() => server.events.removeAllListeners());

describe("platform admin pages", () => {
  it("shows admin navigation and overview to admins, and hides and redirects it for others", async () => {
    const adminApp = renderApp("/", { loggedIn: true });
    expect(await screen.findByRole("link", { name: "Admin" })).toBeInTheDocument();

    adminApp.unmount();
    setMockPlatformAdmin(false);
    renderApp("/admin", { loggedIn: true });
    expect(await screen.findByRole("heading", { name: "Home" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
    expect(screen.getByTestId("current-location")).toHaveTextContent("/");
  });

  it("shows overview values, chart days, and workspace rows", async () => {
    const requests = captureAdminRequests("/api/admin/overview");
    renderApp("/admin", { loggedIn: true });
    expect(await screen.findByRole("heading", { name: "Admin" })).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: /Quality over time/ })).toBeInTheDocument();
    expect(screen.getAllByTestId("chart-bar")).toHaveLength(7);
    expect(within(screen.getByRole("region", { name: "Conversations" })).getByText(/^\d+$/)).toBeInTheDocument();
    const workspacePanel = screen.getByRole("heading", { name: "By workspace" }).parentElement!;
    const workspaceTable = workspacePanel.querySelector<HTMLElement>(".table-wrap")!;
    expect(within(workspaceTable).getByText("Northstar Services")).toBeInTheDocument();
    expect(within(workspaceTable).getByText("Fieldwork Studio")).toBeInTheDocument();
    expect(within(workspaceTable).getByText("Common Ground")).toBeInTheDocument();
    expect(workspacePanel.querySelectorAll('ul[aria-label="Workspace quality"] > li')).toHaveLength(3);
    expect(requests[0].get("days")).toBe("7");
    await userEvent.setup().click(screen.getByRole("button", { name: "30 days" }));
    expect(await screen.findByRole("img", { name: /across 30 days/ })).toBeInTheDocument();
    expect(screen.getByTestId("current-location")).toHaveTextContent("/admin?days=30");
  });

  it("filters needs-review conversations and workspace in the URL and loads another page", async () => {
    const requests = captureAdminRequests("/api/admin/conversations");
    const user = userEvent.setup();
    renderApp("/admin", { loggedIn: true });
    const reviewToggle = await screen.findByRole("checkbox", { name: "Needs review only" });
    await waitFor(() => expect(requests.length).toBeGreaterThan(0));
    expect(requests[0].get("needs_review")).toBe("true");
    const reviewPanel = Array.from(document.querySelectorAll("section.panel"))
      .find((section) => section.querySelector("h2")?.textContent === "Needs review")!;
    await waitFor(() => expect(reviewPanel.querySelector("article a")).toBeTruthy());
    const mobileCard = reviewPanel.querySelector("article")!;
    const mobileTitle = mobileCard.querySelector("a")!;
    expect(mobileCard.firstElementChild).toBe(mobileTitle);
    expect(mobileTitle.textContent).not.toMatch(/conversation/i);
    expect(reviewPanel.querySelector(".table-wrap a")?.textContent).not.toMatch(/conversation/i);

    await user.click(reviewToggle);
    expect(screen.getByTestId("current-location")).toHaveTextContent("review=all");
    await waitFor(() => expect(requests.at(-1)?.has("needs_review")).toBe(false));
    expect(await screen.findByRole("button", { name: "Load more" })).toBeInTheDocument();
    const workspace = screen.getByLabelText("Workspace", { selector: "#admin-workspace" });
    await user.selectOptions(workspace, "71000000-0000-4000-8000-000000000002");
    expect(screen.getByTestId("current-location")).toHaveTextContent("tenant=71000000-0000-4000-8000-000000000002");
    await waitFor(() => expect(requests.at(-1)?.get("tenant_id")).toBe("71000000-0000-4000-8000-000000000002"));

    await user.selectOptions(workspace, "");
    await screen.findByRole("button", { name: "Load more" });
    await user.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(requests.some((query) => query.has("cursor"))).toBe(true));
  });

  it("renders conversation checks, notes, issues, objective metrics, transcript, and trace link", async () => {
    renderApp(`/admin/conversations/${adminConversationDetails[0].id}`, { loggedIn: true });
    expect(await screen.findByRole("heading", { name: /Avery · Northstar Services/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to admin" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Admin navigation" })).not.toBeInTheDocument();
    expect(screen.getByText("Phone")).toBeInTheDocument();
    expect(screen.getByText("Duration 1:22")).toBeInTheDocument();
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("Judge gemini-3.5-flash-lite · rubric v1")).toBeInTheDocument();
    expect(screen.getByText("0:04")).toBeInTheDocument();
    expect(screen.getByText("Collected required details")).toBeInTheDocument();
    expect(screen.getByText("Did not confirm the callback number.")).toBeInTheDocument();
    expect(screen.getByText("Median response")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Conversation transcript" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open trace" })).toHaveAttribute("target", "_blank");
    const scorePanel = screen.getByRole("heading", { name: "Quality score" }).parentElement!;
    expect(scorePanel.querySelector("p")).toHaveTextContent("3 / 5");
    expect(within(scorePanel).queryByText("3/5")).not.toBeInTheDocument();
    expect(within(scorePanel).getByText("Needs review")).toBeInTheDocument();
  });

  it("hides trace when missing and shows empty score state", async () => {
    const conversation = { ...adminConversationDetails[10], trace_url: null, score: null };
    server.use(http.get("*/api/admin/conversations/:id", () => HttpResponse.json(conversation)));
    renderApp(`/admin/conversations/${conversation.id}`, { loggedIn: true });
    expect(await screen.findByText("Not scored yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rescore" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open trace" })).not.toBeInTheDocument();
  });

  it("queues rescoring and creates an eval case with notices", async () => {
    const requests: string[] = [];
    server.events.on("request:start", ({ request }) => {
      if (request.url.includes("/api/admin/conversations/")) requests.push(`${request.method} ${new URL(request.url).pathname}`);
    });
    const user = userEvent.setup();
    renderApp(`/admin/conversations/${adminConversationDetails[0].id}`, { loggedIn: true });
    await user.click(await screen.findByRole("button", { name: "Rescore" }));
    expect(await screen.findByText("Rescore queued")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Make eval case" }));
    expect(await screen.findByText("Eval case created.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View evals" })).toHaveAttribute("href", "/admin/evals");
    expect(requests).toContain(`POST /api/admin/conversations/${adminConversationDetails[0].id}/rescore`);
    expect(requests).toContain(`POST /api/admin/conversations/${adminConversationDetails[0].id}/eval-case`);
  });

  it("lists eval runs and cases and expands failed run results", async () => {
    const user = userEvent.setup();
    renderApp("/admin/evals", { loggedIn: true });
    expect(await screen.findByRole("heading", { name: "Evals" })).toBeInTheDocument();
    expect(await screen.findByText("Callback details are confirmed")).toBeInTheDocument();
    expect(screen.getByText("2 caller turns · 2 checks")).toBeInTheDocument();
    expect(screen.getByText("1 caller turn · 2 checks")).toBeInTheDocument();
    expect(document.querySelectorAll('ul[aria-label="Eval runs"] > li')).toHaveLength(3);
    const runTable = screen.getAllByRole("table")[0];
    await user.click(within(runTable).getAllByRole("button")[0]);
    expect(await within(runTable).findByText("Expected the assistant to read the callback number back.")).toBeInTheDocument();
    expect(screen.getAllByText("Callback details are confirmed").length).toBeGreaterThan(1);
  });

  it("maps score values to badge tones", () => {
    expect(scoreTone(null)).toBe("neutral");
    expect(scoreTone(4)).toBe("success");
    expect(scoreTone(3)).toBe("warning");
    expect(scoreTone(2)).toBe("danger");
  });
});
