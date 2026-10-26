import { http, HttpResponse } from "msw";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockDb, templates } from "../mocks/db";
import { server } from "../mocks/server";
import { renderApp } from "../test/renderApp";

describe("website-based assistant setup", () => {
  it("prefills and automatically reads a query website once in StrictMode", async () => {
    mockDb.memberships[0].name = "Smith Dental";
    let releaseTemplates = () => {};
    const templateGate = new Promise<void>((resolve) => {
      releaseTemplates = resolve;
    });
    server.use(
      http.get("*/api/templates", async () => {
        await templateGate;
        return HttpResponse.json(templates);
      }),
    );
    renderApp("/assistants/new?website=https%3A%2F%2Fsmith-dental.co.uk", { loggedIn: true, strictMode: true });

    try {
      await waitFor(() => expect(screen.getByLabelText("Business name")).toHaveValue("Smith Dental"));
      expect(mockDb.setupDraftRequests).toHaveLength(0);
    } finally {
      releaseTemplates();
    }
    await screen.findByRole("heading", { name: "Choose what to include" });
    expect(mockDb.setupDraftRequests).toHaveLength(1);
    expect(mockDb.setupDraftRequests[0].business_name).toBe("Smith Dental");
    expect(mockDb.setupDraftRequests[0].website_url).toBe("https://smith-dental.co.uk");
  });

  it("uses the website-derived business name when the tenant has no name", async () => {
    mockDb.memberships[0].name = "";
    renderApp("/assistants/new?website=https%3A%2F%2Fsmith-dental.co.uk", { loggedIn: true, strictMode: true });

    await screen.findByRole("heading", { name: "Choose what to include" });
    expect(mockDb.setupDraftRequests).toHaveLength(1);
    expect(mockDb.setupDraftRequests[0].business_name).toBe("Smith Dental");
  });

  it("does not automatically read a website when the query parameter is absent", async () => {
    renderApp("/assistants/new", { loggedIn: true, strictMode: true });
    await waitFor(() => expect(screen.getByRole("radio", { name: /Blank/ })).toBeChecked());
    expect(mockDb.setupDraftRequests).toHaveLength(0);
  });

  it("mirrors the backend validation for an empty draft business name", async () => {
    const response = await fetch("http://localhost/api/assistant-setup/drafts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        business_name: "  ",
        website_url: "https://example.co.uk",
        template: "blank",
      }),
    });

    expect(response.status).toBe(422);
    expect(await response.json()).toMatchObject({
      detail: [{ loc: ["body", "business_name"], type: "string_too_short" }],
    });
    expect(mockDb.setupDraftRequests).toHaveLength(0);
  });

  it("submits only accepted sections and includes edited values", async () => {
    const user = userEvent.setup();
    renderApp("/assistants/new", { loggedIn: true });
    await screen.findByRole("heading", { name: "Create an assistant" });
    expect(await screen.findByRole("radio", { name: /Blank/ })).toBeChecked();
    for (const label of [
      "Blank",
      "UK B2C law firm",
      "Restaurant",
      "Dental or medical clinic",
      "Home services and trades",
      "Real estate and lettings",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    await user.type(screen.getByLabelText("Business name"), "Harbor Dental Studio");
    await user.type(screen.getByLabelText("Website URL (optional)"), "https://harbordental.example");
    await user.click(screen.getByRole("button", { name: "Read website" }));

    const companyCard = await screen.findByRole("region", { name: "Company info" });
    await user.click(within(companyCard).getByRole("button", { name: "Accept" }));
    expect(within(companyCard).queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
    expect(within(companyCard).getByRole("button", { name: "Edit" })).toBeInTheDocument();

    const faqCard = screen.getByRole("region", { name: "FAQs" });
    await user.click(within(faqCard).getByRole("button", { name: "Edit" }));
    const question = within(faqCard).getByLabelText("Question 1");
    await user.clear(question);
    await user.type(question, "Can I book online?");
    await user.click(within(faqCard).getByRole("button", { name: "Save and accept" }));

    const hoursCard = screen.getByRole("region", { name: "Opening hours" });
    await user.click(within(hoursCard).getByRole("button", { name: "Discard" }));

    const servicesCard = screen.getByRole("region", { name: "Suggested services" });
    expect(within(servicesCard).getByText("Name")).toBeInTheDocument();
    expect(within(servicesCard).getByText("Phone number")).toBeInTheDocument();
    expect(within(servicesCard).getByText("Email address")).toBeInTheDocument();
    expect(within(servicesCard).getByText("Reason for your visit")).toBeInTheDocument();
    expect(within(servicesCard).getAllByText("Critical")).toHaveLength(2);
    await user.click(within(servicesCard).getByRole("button", { name: "Discard" }));
    expect(screen.getByText("1 section not reviewed won't be used.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Create assistant" }));

    await waitFor(() => expect(screen.getByTestId("current-location").textContent).toMatch(/^\/assistants\/.+\/settings\/general$/));
    expect(mockDb.setupDraftRequests).toHaveLength(1);
    expect(mockDb.setupCompletions).toHaveLength(1);
    const completion = mockDb.setupCompletions[0];
    expect(completion.accepted).toEqual({
      company_info: "## About\nHarbor Dental Studio provides general dental care for patients in Bristol.",
      faqs: [{ question: "Can I book online?", answer: "Call the practice to arrange an appointment." }],
    });
    expect(completion.accepted).not.toHaveProperty("office_hours");
    expect(completion.accepted).not.toHaveProperty("price_list");
    expect(completion.accepted).not.toHaveProperty("services");
  });

  it("creates an assistant directly when no website is supplied", async () => {
    const user = userEvent.setup();
    renderApp("/assistants/new", { loggedIn: true });
    await screen.findByRole("heading", { name: "Create an assistant" });
    await user.type(screen.getByLabelText("Business name"), "Harbor Dental Studio");
    await user.click(screen.getByRole("button", { name: "Create assistant" }));

    await waitFor(() => expect(screen.getByTestId("current-location").textContent).toMatch(/^\/assistants\/.+\/settings\/general$/));
    expect(mockDb.setupDraftRequests).toHaveLength(0);
    expect(mockDb.setupCompletions).toHaveLength(1);
    expect(mockDb.setupCompletions[0].accepted).toEqual({});
    expect(mockDb.setupCompletions[0].website_url).toBeUndefined();
  });

  it("normalizes a bare domain for setup drafting and completion", async () => {
    const user = userEvent.setup();
    renderApp("/assistants/new", { loggedIn: true });
    await screen.findByRole("heading", { name: "Create an assistant" });
    await user.type(screen.getByLabelText("Business name"), "Harbor Dental Studio");
    const websiteInput = screen.getByLabelText("Website URL (optional)");
    expect(websiteInput).toHaveAttribute("type", "text");
    expect(websiteInput).toHaveAttribute("inputmode", "url");
    expect(websiteInput).toHaveAttribute("autocomplete", "url");
    expect(websiteInput).toHaveAttribute("placeholder", "example.co.uk");
    await user.type(websiteInput, "example.co.uk");
    await user.click(screen.getByRole("button", { name: "Read website" }));

    await screen.findByRole("heading", { name: "Choose what to include" });
    expect(mockDb.setupDraftRequests[0].website_url).toBe("https://example.co.uk");
    await user.click(screen.getByRole("button", { name: "Create assistant" }));

    await waitFor(() => expect(screen.getByTestId("current-location").textContent).toMatch(/^\/assistants\/.+\/settings\/general$/));
    expect(mockDb.setupCompletions[0].website_url).toBe("https://example.co.uk");
  });

  it("offers template defaults after website drafting fails", async () => {
    server.use(
      http.post("*/api/assistant-setup/drafts", () =>
        HttpResponse.json({ detail: "Website drafting is not configured." }, { status: 503 }),
      ),
    );
    const user = userEvent.setup();
    renderApp("/assistants/new", { loggedIn: true });
    await screen.findByRole("heading", { name: "Create an assistant" });
    await user.type(screen.getByLabelText("Business name"), "Harbor Dental Studio");
    await user.type(screen.getByLabelText("Website URL (optional)"), "https://harbordental.example");
    await user.click(screen.getByRole("button", { name: "Read website" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Website drafting is not configured.");
    await user.click(screen.getByRole("button", { name: "Continue with template defaults" }));

    await waitFor(() => expect(screen.getByTestId("current-location").textContent).toMatch(/^\/assistants\/.+\/settings\/general$/));
    expect(mockDb.setupCompletions).toHaveLength(1);
    expect(mockDb.setupCompletions[0].accepted).toEqual({});
    expect(mockDb.setupCompletions[0].website_url).toBe("https://harbordental.example");
  });

  it("creates a demo assistant with selected template sample content", async () => {
    const user = userEvent.setup();
    renderApp("/assistants/new", { loggedIn: true });
    await screen.findByRole("heading", { name: "Create an assistant" });
    const lawCard = (await screen.findByText("UK B2C law firm")).closest("article");
    expect(lawCard).not.toBeNull();
    await user.click(within(lawCard!).getByRole("button", { name: "Create demo assistant" }));

    await waitFor(() => expect(screen.getByTestId("current-location").textContent).toMatch(/^\/assistants\/.+\/settings\/general$/));
    expect(mockDb.setupCompletions).toHaveLength(1);
    expect(mockDb.setupCompletions[0]).toMatchObject({
      name: "UK B2C law firm demo",
      business_name: "Demo & Co Solicitors (sample law firm)",
      template: "uk_law_firm",
      accepted: {},
      use_sample_content: true,
    });
    const created = mockDb.tenants[0].assistants.at(-1);
    expect(created?.config.knowledge?.faqs?.length).toBeGreaterThan(0);
    expect(created?.config.price_list.length).toBeGreaterThan(0);
    expect(mockDb.tenants[0].assistantData[created!.id].digest.content).toContain("SAMPLE CONTENT");
  });
});
