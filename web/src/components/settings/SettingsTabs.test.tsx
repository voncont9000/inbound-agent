import { http, HttpResponse } from "msw";
import { configure, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Schemas } from "../../api/client";
import { settingsTabs } from "../../lib/settingsTabs";
import { mockDb } from "../../mocks/db";
import { server } from "../../mocks/server";
import { renderApp } from "../../test/renderApp";

const assistantId = "20000000-0000-4000-8000-000000000001";
const otherAssistantId = "20000000-0000-4000-8000-000000000002";

configure({ asyncUtilTimeout: 6000 });
const settingsSaveStatus = () => within(screen.getByRole("region", { name: "Settings sections and save status" })).getByRole("status");

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

  it.each(settingsTabs.map(({ id, label }) => [id, label]))("shows the %s tab with global test call and autosave status", async (tab, label) => {
    renderApp(`/assistants/${assistantId}/settings/${tab}`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    const settingsNavigation = screen.getByRole("navigation", { name: "Settings sections" });
    expect(within(settingsNavigation).getByRole("link", { name: label })).toHaveAttribute("aria-current", "page");
    const testCallBar = screen.getByRole("region", { name: "Test call" });
    expect(within(testCallBar).getByRole("button", { name: "Start test call" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Test call" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Help:/ })).toHaveAttribute("href", expect.stringMatching(/^\/help\//));
    expect(settingsSaveStatus()).toHaveTextContent("All changes saved");
    expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();
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
    await waitFor(() => expect(settingsSaveStatus()).toHaveTextContent("All changes saved"), { timeout: 6000 });
  });

  it("shows the translation notice only for a changed single language and applies the draft translation", async () => {
    const user = userEvent.setup();
    let requestBody: Schemas["TranslateLinesRequest"] | undefined;
    const requestLocales: Schemas["TranslateLinesRequest"]["locale"][] = [];
    mockDb.tenants[0].assistants[0].config.language = "it-IT";
    server.use(
      http.post<{ id: string }, Schemas["TranslateLinesRequest"], Schemas["TranslateLinesResponse"]>(
        "*/api/assistants/:id/translate-lines",
        async ({ request }) => {
          const body = await request.json() as Schemas["TranslateLinesRequest"];
          requestBody = body;
          requestLocales.push(body.locale);
          const italian = body.locale === "it-IT";
          return HttpResponse.json<Schemas["TranslateLinesResponse"]>({
            greeting: italian
              ? "Ciao, parli con l’assistente AI di Brightside Plumbing & Heating."
              : "Hello, you are speaking with the AI assistant for Brightside Plumbing & Heating.",
            call_settings: {
              max_call_message: italian ? "Abbiamo raggiunto il limite di durata della chiamata." : "We have reached the maximum call duration.",
              still_there_prompt: italian ? "È ancora in linea?" : "Are you still there?",
              silence_goodbye_message: italian ? "Terminerò la chiamata. Grazie per aver chiamato." : "I’ll end the call now. Thank you for calling.",
            },
            transfer_handoff_message: italian ? "Ora la trasferisco, resti in linea." : "I’ll transfer you now; please hold.",
            transfer_failed_message: italian ? "Mi dispiace, nessuno è disponibile al momento." : "I’m sorry, nobody is available right now.",
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

    await user.selectOptions(language, "en-GB");
    const translateToEnglish = await screen.findByRole("button", { name: "Translate them to English (UK)" });
    await user.click(translateToEnglish);
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: "Greeting" })).toHaveValue(
        "Hello, you are speaking with the AI assistant for Brightside Plumbing & Heating.",
      );
      expect(screen.queryByRole("button", { name: /Translate them to/ })).not.toBeInTheDocument();
    });

    await user.selectOptions(language, "it-IT");
    const translateToItalian = await screen.findByRole("button", { name: "Translate them to Italian" });
    await user.click(translateToItalian);

    await waitFor(() => expect(screen.getByRole("textbox", { name: "Greeting" })).toHaveValue(
      "Ciao, parli con l’assistente AI di Brightside Plumbing & Heating.",
    ));
    await waitFor(() => expect(settingsSaveStatus()).toHaveTextContent("All changes saved"), { timeout: 6000 });
    await waitFor(() => expect(
      screen.queryByRole("button", { name: /Translate them to/ }),
    ).not.toBeInTheDocument());
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
    expect(requestLocales).toEqual(["en-GB", "it-IT"]);
    await user.click(screen.getByRole("link", { name: "Advanced" }));
    expect(await screen.findByLabelText("Maximum-call message")).toHaveValue(
      "Abbiamo raggiunto il limite di durata della chiamata.",
    );
    expect(screen.getByLabelText("Still-there prompt")).toHaveValue("È ancora in linea?");
    expect(screen.getByLabelText("Silence goodbye message")).toHaveValue(
      "Terminerò la chiamata. Grazie per aver chiamato.",
    );
    await waitFor(() => expect(settingsSaveStatus()).toHaveTextContent("All changes saved"), { timeout: 6000 });
  }, 10000);

  it("hides the translation notice when switching back to the saved language without translating", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    const language = await screen.findByLabelText("Language");

    await user.selectOptions(language, "it-IT");
    expect(await screen.findByRole("button", { name: "Translate them to Italian" })).toBeInTheDocument();

    await user.selectOptions(language, "en-GB");
    await waitFor(() => expect(language).toHaveValue("en-GB"));

    expect(screen.queryByRole("button", { name: /Translate them to/ })).not.toBeInTheDocument();
  });

  it("keeps the translation notice after saving a language change without translating", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });

    await user.selectOptions(await screen.findByLabelText("Language"), "it-IT");
    expect(await screen.findByRole("button", { name: "Translate them to Italian" })).toBeInTheDocument();
    await waitFor(() => expect(settingsSaveStatus()).toHaveTextContent("All changes saved"), { timeout: 6000 });
    expect(screen.getByRole("button", { name: "Translate them to Italian" })).toBeInTheDocument();
  }, 10000);

  it("autosaves a draft after the debounce and reports each save state", async () => {
    const user = userEvent.setup();
    const assistant = mockDb.tenants[0].assistants[0];
    let requestCount = 0;
    let requestBody: Schemas["AssistantUpdate"] | undefined;
    let releaseRequest = () => {};
    const requestGate = new Promise<void>((resolve) => { releaseRequest = resolve; });
    server.use(http.put("*/api/assistants/:id", async ({ request }) => {
      requestCount += 1;
      requestBody = await request.json() as Schemas["AssistantUpdate"];
      await requestGate;
      return HttpResponse.json({ ...assistant, ...requestBody });
    }));
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    const greeting = screen.getByRole("textbox", { name: "Greeting" });
    await user.type(greeting, " Welcome.");
    const status = within(screen.getByRole("region", { name: "Settings sections and save status" })).getByRole("status");
    expect(status).toHaveTextContent("Unsaved changes");

    await waitFor(() => expect(requestCount).toBe(1), { timeout: 6000 });
    expect(requestBody?.config.greeting).toContain("Welcome.");
    expect(status).toHaveTextContent("Saving…");
    releaseRequest();
    await waitFor(() => expect(status).toHaveTextContent("All changes saved"), { timeout: 6000 });
    expect(requestCount).toBe(1);
  }, 15000);

  it("keeps edits made during a save and sends the newer draft afterward", async () => {
    const user = userEvent.setup();
    const assistant = mockDb.tenants[0].assistants[0];
    const requestBodies: Schemas["AssistantUpdate"][] = [];
    let releaseFirstRequest = () => {};
    const firstRequestGate = new Promise<void>((resolve) => { releaseFirstRequest = resolve; });
    server.use(http.put("*/api/assistants/:id", async ({ request }) => {
      const body = await request.json() as Schemas["AssistantUpdate"];
      requestBodies.push(body);
      if (requestBodies.length === 1) await firstRequestGate;
      return HttpResponse.json({ ...assistant, ...body });
    }));
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    const greeting = screen.getByRole("textbox", { name: "Greeting" });
    await user.type(greeting, " First draft.");
    await waitFor(() => expect(requestBodies).toHaveLength(1), { timeout: 6000 });
    await user.type(greeting, " Latest edit.");
    releaseFirstRequest();
    await waitFor(() => expect(requestBodies).toHaveLength(2), { timeout: 7000 });
    expect(requestBodies[1].config.greeting).toContain("Latest edit.");
    await waitFor(() => expect(settingsSaveStatus()).toHaveTextContent("All changes saved"), { timeout: 6000 });
    expect(greeting).toHaveValue(requestBodies[1].config.greeting);
  }, 20000);

  it("does not send an invalid draft and explains that it needs fixing", async () => {
    const user = userEvent.setup();
    let putCount = 0;
    const listener = ({ request }: { request: Request }) => {
      if (request.method === "PUT" && request.url.includes("/assistants/")) putCount += 1;
    };
    server.events.on("request:start", listener);
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.click(screen.getByRole("button", { name: "Edit services and intake fields" }));
    const choices = screen.getAllByLabelText("Choices")[0];
    await user.clear(choices);

    await waitFor(() => expect(settingsSaveStatus()).toHaveTextContent("Not saved: fix the highlighted fields"), { timeout: 6000 });
    await new Promise<void>((resolve) => window.setTimeout(resolve, 0));
    expect(choices).toHaveAttribute("aria-invalid", "true");
    expect(choices).toHaveFocus();
    expect(screen.getByTestId("current-location")).toHaveTextContent(`/assistants/${assistantId}/settings/general`);
    expect(putCount).toBe(0);
    server.events.removeListener("request:start", listener);
  }, 10000);

  it("does not navigate away from the edited tab when another tab has validation errors", async () => {
    const user = userEvent.setup();
    const assistant = mockDb.tenants[0].assistants.find(({ id }) => id === assistantId)!;
    assistant.config.rules!.pronunciations = [{ term: "", say_as: "" }];
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    const greeting = screen.getByRole("textbox", { name: "Greeting" });
    await user.type(greeting, " Keep editing.");

    await waitFor(() => expect(settingsSaveStatus()).toHaveTextContent("Not saved: fix the highlighted fields"), { timeout: 6000 });
    expect(screen.getByTestId("current-location")).toHaveTextContent(`/assistants/${assistantId}/settings/general`);
    expect(greeting).toHaveFocus();
  }, 10000);

  it("does not move focus for server validation returned during autosave", async () => {
    const user = userEvent.setup();
    server.use(http.put("*/api/assistants/:id", () => HttpResponse.json({
      detail: [{ loc: ["body", "config", "greeting"], msg: "Server rejected greeting", type: "value_error" }],
    }, { status: 422 })));
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    const greeting = screen.getByRole("textbox", { name: "Greeting" });
    await user.type(greeting, " Rejected.");

    await waitFor(() => expect(settingsSaveStatus()).toHaveTextContent("Not saved: fix the highlighted fields"), { timeout: 6000 });
    expect(await screen.findByRole("link", { name: "Server rejected greeting" })).toBeInTheDocument();
    await new Promise<void>((resolve) => window.setTimeout(resolve, 0));
    expect(greeting).toHaveFocus();
    expect(screen.getByTestId("current-location")).toHaveTextContent(`/assistants/${assistantId}/settings/general`);
  }, 10000);

  it("retries a server error once, then offers a manual retry", async () => {
    const user = userEvent.setup();
    let putCount = 0;
    server.use(http.put("*/api/assistants/:id", () => {
      putCount += 1;
      return HttpResponse.json({ detail: "Temporary failure" }, { status: 500 });
    }));
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.type(screen.getByRole("textbox", { name: "Greeting" }), " Retry me.");

    await waitFor(() => expect(settingsSaveStatus()).toHaveTextContent("Couldn't save. Retrying…"), { timeout: 6000 });
    await waitFor(() => expect(putCount).toBe(2), { timeout: 6000 });
    await waitFor(() => expect(settingsSaveStatus()).toHaveTextContent("Couldn't save"), { timeout: 6000 });
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  }, 15000);

  it("flushes a pending autosave when navigating to another settings tab", async () => {
    const user = userEvent.setup();
    let requestBody: Schemas["AssistantUpdate"] | undefined;
    server.use(http.put("*/api/assistants/:id", async ({ request }) => {
      requestBody = await request.json() as Schemas["AssistantUpdate"];
      return HttpResponse.json({ ...mockDb.tenants[0].assistants[0], ...requestBody });
    }));
    renderApp(`/assistants/${assistantId}/settings/general`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    await user.type(screen.getByRole("textbox", { name: "Greeting" }), " Flush on navigation.");
    await user.click(screen.getByRole("link", { name: "Rules" }));

    await waitFor(() => expect(requestBody?.config.greeting).toContain("Flush on navigation."), { timeout: 6000 });
    expect(screen.getByTestId("current-location")).toHaveTextContent(`/assistants/${assistantId}/settings/rules`);
  }, 10000);

  it("keeps Readback in Advanced and clears caller confirmation when turned off", async () => {
    const user = userEvent.setup();
    renderApp(`/assistants/${assistantId}/settings/abilities`, { loggedIn: true });
    await screen.findByRole("heading", { name: "Brightside reception" });
    expect(screen.queryByRole("button", { name: /Readback/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "Advanced" }));
    const readback = screen.getByRole("region", { name: "Settings sections and save status" }).parentElement;
    expect(readback).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Readback" })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /confirm critical fields/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /Read back every field marked critical/ }));
    let confirmation = screen.getByRole("checkbox", { name: /Record the caller's confirmation/ });
    if (!(confirmation as HTMLInputElement).checked) await user.click(confirmation);
    await user.click(screen.getByRole("radio", { name: /Off.*Don't read details back/ }));
    confirmation = screen.getByRole("checkbox", { name: /Record the caller's confirmation/ });
    expect(confirmation).toBeDisabled();
    expect(confirmation).not.toBeChecked();
  });
});
