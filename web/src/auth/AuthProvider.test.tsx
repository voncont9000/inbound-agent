import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderApp } from "../test/renderApp";

describe("workspace switching", () => {
  it("refetches tenant data for the selected workspace", async () => {
    renderApp("/assistants", { loggedIn: true });
    await screen.findAllByText("Brightside reception");
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText("Workspace"), "10000000-0000-4000-8000-000000000002");

    await waitFor(() => expect(screen.queryAllByText("Brightside reception")).toHaveLength(0));
    expect(JSON.parse(localStorage.getItem("inbound.memberships") ?? "[]")).toHaveLength(2);
    expect(screen.getAllByText("Demo & Co Solicitors").length).toBeGreaterThan(1);
  });
});
