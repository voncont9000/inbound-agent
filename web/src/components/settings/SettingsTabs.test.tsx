import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { settingsTabs } from "../../lib/settingsTabs";
import { renderApp } from "../../test/renderApp";

const assistantId = "20000000-0000-4000-8000-000000000001";

describe("assistant settings tabs", () => {
  it.each([
    ["/settings", "general"],
    ["/knowledge", "knowledge"],
    ["/settings/unknown", "general"],
  ])("redirects %s to the %s tab", async (suffix, tab) => {
    renderApp(`/assistants/${assistantId}${suffix}`, { loggedIn: true });
    await waitFor(() => expect(screen.getByTestId("current-location")).toHaveTextContent(`/assistants/${assistantId}/settings/${tab}`));
    expect(await screen.findByRole("heading", { name: "Brightside reception" })).toBeInTheDocument();
  });

  it.each(settingsTabs.map(({ id, label }) => [id, label]))("shows the %s tab with global test call and Save controls", async (tab, label) => {
    renderApp(`/assistants/${assistantId}/settings/${tab}`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    expect(screen.getByRole("link", { name: label })).toHaveAttribute("aria-current", "page");
    const testCallBar = screen.getByRole("region", { name: "Test call" });
    expect(within(testCallBar).getByRole("button", { name: "Start test call" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Test call" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Help:/ })).toHaveAttribute("href", expect.stringMatching(/^\/help\//));
    expect(screen.getByRole("button", { name: "Save changes" })).toBeInTheDocument();
  });

  it("renders the contextual Help link inside the settings tab row", async () => {
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    const navigation = screen.getByRole("navigation", { name: "Settings sections" });
    const helpLink = screen.getByRole("link", { name: "Help: Set the assistant name, language and voice" });

    expect(navigation.parentElement).toContainElement(helpLink);
    expect(helpLink).toHaveTextContent("? Help");
    expect(helpLink).toHaveAttribute("title", "Help: Set the assistant name, language and voice");
  });

  it("puts the knowledge page content on Knowledge and the rule editor on Rules", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings/knowledge`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    for (const heading of ["Knowledge sources", "Knowledge digest", "Frequently asked questions", "Office hours"]) {
      expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
    }
    await user.click(screen.getByRole("link", { name: "Rules" }));
    expect(screen.getByRole("heading", { name: "Rules" })).toBeInTheDocument();
    expect(screen.getByLabelText("AI disclosure")).toBeInTheDocument();
  });

  it("normalizes a bare domain before adding a knowledge source", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings/knowledge`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });

    const websiteInput = screen.getByLabelText("Website URL");
    expect(websiteInput).toHaveAttribute("type", "text");
    expect(websiteInput).toHaveAttribute("inputmode", "url");
    expect(websiteInput).toHaveAttribute("autocomplete", "url");
    expect(websiteInput).toHaveAttribute("placeholder", "example.co.uk");
    await user.type(websiteInput, "example.co.uk/about");
    await user.click(screen.getByRole("button", { name: "Add website" }));

    expect(await screen.findByText(/https:\/\/example\.co\.uk\/about/)).toBeInTheDocument();
  });

  it("keeps abilities off until switched on and keeps unsaved edits across tabs", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    expect(screen.getByText("All changes saved")).toBeInTheDocument();
    expect(await screen.findByLabelText("Language")).toHaveValue("en-GB");
    expect(await screen.findByRole("radio", { name: /Beatrice/i })).toBeChecked();
    await user.type(screen.getByLabelText("Business name"), " Ltd");
    await user.click(screen.getByRole("link", { name: "Abilities" }));
    expect(screen.getByRole("switch", { name: "Calendar booking" })).not.toBeChecked();
    expect(screen.getByRole("switch", { name: "Call forwarding" })).not.toBeChecked();
    expect(screen.queryByRole("button", { name: "Add appointment type" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "General" }));
    expect(screen.getByLabelText("Business name")).toHaveValue("Brightside Plumbing & Heating Ltd");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
  });
});
