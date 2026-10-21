import { http, HttpResponse } from "msw";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Schemas } from "../../api/client";
import { settingsTabs } from "../../lib/settingsTabs";
import { server } from "../../mocks/server";
import { renderApp } from "../../test/renderApp";

const assistantId = "20000000-0000-4000-8000-000000000001";
const otherAssistantId = "20000000-0000-4000-8000-000000000002";

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
    const settingsNavigation = screen.getByRole("navigation", { name: "Settings sections" });
    expect(within(settingsNavigation).getByRole("link", { name: label })).toHaveAttribute("aria-current", "page");
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

  it("switches assistants while preserving the selected settings tab", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("*/api/assistants", () => HttpResponse.json({ items: [
        { id: assistantId, name: "Brightside reception" },
        { id: otherAssistantId, name: "Harbor assistant" },
      ] })),
      http.get("*/api/assistants/:id/channels", ({ params }) => HttpResponse.json({
        items: params.id === assistantId
          ? [{
            id: "phone-1", assistant_id: assistantId, kind: "phone", external_id: "+44 20 7946 0958",
            status: "active", settings: {}, created_at: "2026-06-01T00:00:00Z", google_features: [],
          }]
          : [],
      })),
    );
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    const switcher = screen.getByRole("button", { name: "Switch assistant" });
    await waitFor(() => expect(switcher).toHaveTextContent("+44 20 7946 0958"));
    await user.click(switcher);
    await user.type(screen.getByLabelText("Search assistants"), "Harbor");
    await user.click(screen.getByRole("link", { name: /Harbor assistant/ }));
    await waitFor(() => expect(screen.getByTestId("current-location")).toHaveTextContent(
      `/assistants/${otherAssistantId}/settings/abilities`,
    ));
  });

  it("links the assistant channel summary to workspace channel management", async () => {
    renderApp(`/assistants/${assistantId}/settings/channels`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });

    expect(await screen.findByRole("link", { name: "Manage channels" })).toHaveAttribute(
      "href",
      `/channels?assistant=${assistantId}`,
    );
  });

  it("closes the assistant switcher on Escape and restores focus", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    const trigger = screen.getByRole("button", { name: "Switch assistant" });

    await user.click(trigger);
    expect(await screen.findByRole("searchbox", { name: "Search assistants" })).toBeInTheDocument();
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("searchbox", { name: "Search assistants" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("closes the assistant switcher when clicking outside", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: "Switch assistant" }));
    expect(await screen.findByRole("searchbox", { name: "Search assistants" })).toBeInTheDocument();

    await user.click(screen.getByRole("heading", { name: "Brightside reception" }));

    expect(screen.queryByRole("searchbox", { name: "Search assistants" })).not.toBeInTheDocument();
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
    expect(screen.getByLabelText("Language").closest(".field-row")).toBeInTheDocument();
    expect(await screen.findByRole("radio", { name: /Beatrice/i })).toBeChecked();
    await user.type(screen.getByLabelText("Business name"), " Ltd");
    await user.click(screen.getByRole("link", { name: "Abilities" }));
    expect(screen.getByRole("switch", { name: "Calendar booking" })).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: /Call forwarding/ }));
    expect(screen.getByRole("switch", { name: "Call forwarding" })).not.toBeChecked();
    expect(screen.queryByRole("button", { name: "Add appointment type" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "General" }));
    expect(screen.getByLabelText("Business name")).toHaveValue("Brightside Plumbing & Heating Ltd");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
  });

  it("shows the translation notice only for a changed single language and applies the draft translation", async () => {
    const user = userEvent.setup();
    let requestBody: Schemas["TranslateLinesRequest"] | undefined;
    server.use(
      http.post<{ id: string }, Schemas["TranslateLinesRequest"], Schemas["TranslateLinesResponse"]>(
        "*/api/assistants/:id/translate-lines",
        async ({ request }) => {
          requestBody = await request.json() as Schemas["TranslateLinesRequest"];
          return HttpResponse.json<Schemas["TranslateLinesResponse"]>({
            greeting: "Ciao, parli con l’assistente AI di Brightside Plumbing & Heating.",
            call_settings: {
              max_call_message: "Abbiamo raggiunto il limite di durata della chiamata.",
              still_there_prompt: "È ancora in linea?",
              silence_goodbye_message: "Terminerò la chiamata. Grazie per aver chiamato.",
            },
            transfer_handoff_message: "Ora la trasferisco, resti in linea.",
            transfer_failed_message: "Mi dispiace, nessuno è disponibile al momento.",
          });
        },
      ),
    );
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    const language = await screen.findByLabelText("Language");
    expect(screen.queryByRole("button", { name: /Translate them to/ })).not.toBeInTheDocument();

    await user.selectOptions(language, "multi");
    await waitFor(() => expect(language).toHaveValue("multi"));
    expect(screen.queryByRole("button", { name: /Translate them to/ })).not.toBeInTheDocument();

    await user.selectOptions(language, "it-IT");
    const translate = await screen.findByRole("button", { name: "Translate them to Italian" });
    await user.click(translate);

    expect(await screen.findByRole("textbox", { name: "Greeting" })).toHaveValue(
      "Ciao, parli con l’assistente AI di Brightside Plumbing & Heating.",
    );
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    await waitFor(() => expect(requestBody).toMatchObject({
      locale: "it-IT",
      lines: {
        greeting: expect.any(String),
        call_settings: {
          max_call_message: expect.any(String),
          still_there_prompt: expect.any(String),
          silence_goodbye_message: expect.any(String),
        },
        transfer_handoff_message: expect.any(String),
        transfer_failed_message: expect.any(String),
      },
    }));
    await user.click(screen.getByRole("link", { name: "Advanced" }));
    expect(await screen.findByLabelText("Maximum-call message")).toHaveValue(
      "Abbiamo raggiunto il limite di durata della chiamata.",
    );
    expect(screen.getByLabelText("Still-there prompt")).toHaveValue("È ancora in linea?");
    expect(screen.getByLabelText("Silence goodbye message")).toHaveValue(
      "Terminerò la chiamata. Grazie per aver chiamato.",
    );
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
  });
});
