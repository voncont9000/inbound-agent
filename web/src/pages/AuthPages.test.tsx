import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { server } from "../mocks/server";
import { mockDb } from "../mocks/db";
import { renderApp } from "../test/renderApp";

describe("signup", () => {
  it("validates locally, retains details on Back, and starts setup with the website", async () => {
    const user = userEvent.setup();
    renderApp("/signup?website=smith-dental.co.uk");

    const email = screen.getByLabelText("Email address");
    const password = screen.getByLabelText("Password");
    expect(email).toHaveFocus();
    expect(screen.getByText("Step 1 of 2")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter your email address.");

    await user.type(email, "hello@smith-dental.example");
    await user.type(password, "long-enough-password");
    await user.click(screen.getByRole("button", { name: "Show password" }));
    expect(password).toHaveAttribute("type", "text");
    await user.click(screen.getByRole("button", { name: "Hide password" }));
    expect(password).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(screen.getByText("Step 2 of 2")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "About your business" })).toBeInTheDocument();
    const name = screen.getByLabelText("Your name");
    const businessName = screen.getByLabelText("Business name");
    const website = screen.getByLabelText("Website (optional)");
    expect(name).toHaveFocus();
    expect(businessName).toHaveValue("Smith Dental");
    expect(website).toHaveValue("smith-dental.co.uk");
    expect(screen.getByRole("link", { name: "privacy policy" })).toHaveAttribute("href", "https://yelvo.vinci-labs.tech/privacy");

    await user.type(name, "Alex Smith");
    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByLabelText("Email address")).toHaveValue("hello@smith-dental.example");
    expect(screen.getByLabelText("Password")).toHaveValue("long-enough-password");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByLabelText("Your name")).toHaveValue("Alex Smith");
    expect(screen.getByLabelText("Business name")).toHaveValue("Smith Dental");
    expect(screen.getByLabelText("Website (optional)")).toHaveValue("smith-dental.co.uk");

    await user.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(screen.getByTestId("current-location")).toHaveTextContent("/assistants/new?website=https%3A%2F%2Fsmith-dental.co.uk"));
    expect(mockDb.memberships.at(-1)?.name).toBe("Smith Dental");
    expect(mockDb.user.display_name).toBe("Alex Smith");
  });

  it("uses a safe next path before the website setup destination", async () => {
    const user = userEvent.setup();
    renderApp("/signup?website=smith-dental.co.uk&next=%2Finbox%3Ffilter%3Dneeds-review");

    await user.type(screen.getByLabelText("Email address"), "hello@smith-dental.example");
    await user.type(screen.getByLabelText("Password"), "long-enough-password");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.type(screen.getByLabelText("Your name"), "Alex Smith");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    await waitFor(() => expect(screen.getByTestId("current-location")).toHaveTextContent("/inbox?filter=needs-review"));
  });

  it("returns to step 1 with a sign-in link when the email already exists", async () => {
    server.use(
      http.post("*/api/auth/signup", () =>
        HttpResponse.json({ detail: "An account already exists." }, { status: 409 }),
      ),
    );
    const user = userEvent.setup();
    renderApp("/signup");

    await user.type(screen.getByLabelText("Email address"), "hello@example.test");
    await user.type(screen.getByLabelText("Password"), "long-enough-password");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.type(screen.getByLabelText("Your name"), "Alex Smith");
    await user.type(screen.getByLabelText("Business name"), "Example business");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("An account with this email already exists.");
    expect(withinAlertLink(alert)).toHaveAttribute("href", "/login");
    expect(screen.getByText("Step 1 of 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Email address")).toHaveValue("hello@example.test");
    expect(screen.getByLabelText("Password")).toHaveValue("long-enough-password");
  });
});

function withinAlertLink(alert: HTMLElement): HTMLAnchorElement {
  return alert.querySelector("a") as HTMLAnchorElement;
}
