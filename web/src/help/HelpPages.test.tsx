import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { HelpArticlePage } from "./HelpPages";
import { HelpLink } from "./HelpLink";
import { renderApp } from "../test/renderApp";

describe("help centre pages", () => {
  it("shows categories and a Help link in main navigation", async () => {
    renderApp("/help", { loggedIn: true });
    expect(await screen.findByRole("heading", { name: "Help center" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Help" })).toHaveAttribute("href", "/help");
    expect(screen.getAllByRole("heading", { name: "Getting started" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("heading", { name: "Channels" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: "Connect Gmail" }).length).toBeGreaterThan(0);
  });

  it("filters search results and reports when there are no matches", async () => {
    const user = userEvent.setup();
    renderApp("/help", { loggedIn: true });
    const search = await screen.findByRole("searchbox", { name: "Search help articles" });
    await user.type(search, "gmail");
    const results = screen.getByRole("region", { name: "Search results" });
    expect(within(results).getByRole("link", { name: "Connect Gmail" })).toBeInTheDocument();
    await user.clear(search);
    await user.type(search, "not-a-real-help-topic");
    expect(screen.getByText(/No articles match/)).toBeInTheDocument();
  });

  it("shows an article, related links and persists feedback", async () => {
    const user = userEvent.setup();
    const view = renderApp("/help/assistant-settings/rules", { loggedIn: true });
    expect(await screen.findByRole("heading", { name: "Set conversation rules" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Related articles" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Set conversation rules" })).toHaveAttribute("aria-current", "page");

    await user.click(screen.getByRole("button", { name: "Yes" }));
    expect(screen.getByText("Thanks for your feedback.")).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("inbound.help.feedback") ?? "{}")).toEqual({
      "assistant-settings/rules": "yes",
    });
    view.unmount();

    render(
      <MemoryRouter initialEntries={["/help/assistant-settings/rules"]}>
        <Routes><Route path="/help/:category/:article" element={<HelpArticlePage />} /></Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("Thanks for your feedback.")).toBeInTheDocument();
  });

  it("shows a not-found page for an unknown article", async () => {
    renderApp("/help/unknown/missing", { loggedIn: true });
    expect(await screen.findByRole("heading", { name: "Article not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Help center" })).toHaveAttribute("href", "/help");
  });

  it("navigates internal Markdown links in the app", async () => {
    const user = userEvent.setup();
    renderApp("/help/getting-started/create-from-website", { loggedIn: true });
    const article = await screen.findByRole("article");
    await user.click(within(article).getByRole("link", { name: "Assistants" }));
    expect(await screen.findByRole("heading", { name: "Assistants" })).toBeInTheDocument();
    expect(screen.getByTestId("current-location")).toHaveTextContent("/assistants");
  });

  it("renders a contextual help link with its article title", () => {
    render(
      <MemoryRouter>
        <HelpLink to="assistant-settings/rules" />
      </MemoryRouter>,
    );
    const link = screen.getByRole("link", { name: "Help: Set conversation rules" });
    expect(link).toHaveAttribute("href", "/help/assistant-settings/rules");
    expect(link).toHaveAttribute("title", "Help: Set conversation rules");
  });

  it("includes the contextual help link on assistant settings", async () => {
    renderApp("/assistants/20000000-0000-4000-8000-000000000001/settings/general", { loggedIn: true });
    expect(await screen.findByRole("link", { name: "Help: Set the assistant name, language and voice" })).toBeInTheDocument();
  });
});
