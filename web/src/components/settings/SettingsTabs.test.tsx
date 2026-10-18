import { screen, waitFor } from "@testing-library/react";
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

  it.each(settingsTabs.map(({ id, label }) => [id, label]))("shows the %s tab with Test call and Save controls", async (tab, label) => {
    renderApp(`/assistants/${assistantId}/settings/${tab}`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    expect(screen.getByRole("link", { name: label })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Test call" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeInTheDocument();
  });

  it("puts the knowledge page content on the Knowledge tab and keeps a slot on Rules", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings/knowledge`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    for (const heading of ["Knowledge sources", "Knowledge digest", "Frequently asked questions", "Office hours", "Price list"]) {
      expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
    }
    await user.click(screen.getByRole("link", { name: "Rules" }));
    expect(screen.getByRole("heading", { name: "Policies" })).toBeInTheDocument();
  });

  it("keeps abilities off until switched on and keeps unsaved edits across tabs", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    expect(screen.getByText(/Version \d+ is up to date/)).toBeInTheDocument();
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
