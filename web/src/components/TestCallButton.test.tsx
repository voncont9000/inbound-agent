import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { server } from "../mocks/server";
import { renderApp } from "../test/renderApp";

const assistantId = "20000000-0000-4000-8000-000000000001";

describe("assistant test calls", () => {
  it("shows the generated test-call link", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: "Test call" }));

    const link = await screen.findByRole("link", { name: "Open test call" });
    expect(link).toHaveAttribute(
      "href",
      "http://127.0.0.1:7862/?call_token=mock-call-token",
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener");
    expect(screen.getByText("Link valid for 10 minutes")).toBeInTheDocument();
  });

  it("shows the API detail when test calls are not configured", async () => {
    server.use(
      http.post("*/api/assistants/:id/test-calls", () =>
        HttpResponse.json({ detail: "Test calls are not configured" }, { status: 503 }),
      ),
    );
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: "Test call" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Test calls are not configured",
    );
  });
});
