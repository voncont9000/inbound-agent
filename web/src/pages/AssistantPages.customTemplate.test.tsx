import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { server } from "../mocks/server";
import { renderApp } from "../test/renderApp";

afterEach(() => server.events.removeAllListeners());

describe("custom template", () => {
  it("generates a template from sector and use case and creates the assistant with it", async () => {
    const created: Array<{ template: string | null; config: { policies: string[]; services: Record<string, unknown> } }> = [];
    server.events.on("request:start", async ({ request }) => {
      if (request.method === "POST" && new URL(request.url).pathname.endsWith("/api/assistants")) {
        created.push(await request.clone().json());
      }
    });
    const user = userEvent.setup();
    renderApp("/assistants", { loggedIn: true });
    await user.click(await screen.findByRole("button", { name: "Create from template" }));
    await user.click(await screen.findByRole("radio", { name: /Custom template/ }));
    await user.selectOptions(await screen.findByLabelText("Sector"), "Automotive and garages");
    await user.selectOptions(screen.getByLabelText("Use case"), "Take bookings and reservations");
    await user.type(screen.getByLabelText("Anything else (optional)"), "We also do tyres");
    expect(screen.getByRole("button", { name: "Create assistant" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Generate template" }));
    expect(await screen.findByRole("heading", { name: "Automotive and garages take bookings and reservations" })).toBeInTheDocument();
    expect(screen.getByText("Take bookings and reservations intake")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Assistant name"), "Garage");
    await user.click(screen.getByRole("button", { name: "Create assistant" }));
    await waitFor(() => expect(created).toHaveLength(1));
    expect(created[0].template).toBeNull();
    expect(Object.keys(created[0].config.services)).toEqual(["main_request"]);
    expect(created[0].config.policies[0]).toContain("automotive and garages");
  });
});
