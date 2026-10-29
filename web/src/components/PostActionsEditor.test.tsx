import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { AssistantConfig } from "../lib/config";
import { EMAIL_ACTION_DEFAULTS } from "../lib/emailActionDefaults";
import { emailActionTemplates, templates } from "../mocks/fixtures";
import { PostActionsEditor } from "./PostActionsEditor";

type PostAction = NonNullable<AssistantConfig["post_actions"]>[number];

function makeConfig(action?: PostAction) {
  const config = structuredClone(templates[0].config) as AssistantConfig;
  config.post_actions = action ? [action] : [];
  return config;
}

function renderEditor(initialConfig: AssistantConfig) {
  function Harness() {
    const [config, setConfig] = useState(initialConfig);
    return <PostActionsEditor
      assistantId="assistant-1"
      config={config}
      update={(change) => setConfig((current) => {
        const next = structuredClone(current);
        change(next);
        return next;
      })}
      saveDraft={async () => true}
    />;
  }
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Harness />
    </QueryClientProvider>,
  );
}

const inquiry = emailActionTemplates.find(({ key }) => key === "inquiry_confirmation")!;
const requestReceived = emailActionTemplates.find(({ key }) => key === "request_received")!;

describe("PostActionsEditor email templates", () => {
  it("keeps new-action defaults in sync with the core mock templates", () => {
    expect(EMAIL_ACTION_DEFAULTS.email_customer).toMatchObject({
      key: inquiry.key,
      subject_template: inquiry.subject_template,
      body_template: inquiry.body_template,
      translate_to_customer_language: true,
    });
    const staff = emailActionTemplates.find(({ key }) => key === "staff_new_enquiry")!;
    expect(EMAIL_ACTION_DEFAULTS.notify_staff).toMatchObject({
      key: staff.key,
      subject_template: staff.subject_template,
      body_template: staff.body_template,
    });
  });

  it("replaces the subject and body when a template is selected", async () => {
    const action: PostAction = {
      kind: "email_customer",
      key: "customer-email",
      enabled: true,
      trigger: { events: ["conversation_completed"], channels: [], services: [] },
      subject_template: inquiry.subject_template,
      body_template: inquiry.body_template,
      translate_to_customer_language: true,
    };
    const user = userEvent.setup();
    renderEditor(makeConfig(action) as AssistantConfig);
    await user.click(await screen.findByRole("button", { name: /Expand Email the customer settings/ }));

    await user.selectOptions(
      await screen.findByRole("combobox", { name: "Start from a template" }),
      "request_received",
    );

    expect(screen.getByLabelText("Subject template 1")).toHaveValue("Your request – {{business_name}}");
    const body = screen.getByLabelText("Body template 1");
    expect(body).toHaveValue(requestReceived.body_template);
    expect((body as HTMLTextAreaElement).value).toContain("Thank you for your request.");
    expect((body as HTMLTextAreaElement).value).toContain("{{contact.first_name|there}}");
    fireEvent.change(body, { target: { value: "Hello {{contact.first_name|there}}." } });
    expect(body).toHaveValue("Hello {{contact.first_name|there}}.");
  });

  it("enables customer-language translation by default and updates the config when toggled", async () => {
    const action = {
      kind: "email_customer",
      key: "customer-email",
      enabled: true,
      trigger: { events: ["conversation_completed"], channels: [], services: [] },
      subject_template: inquiry.subject_template,
      body_template: inquiry.body_template,
    } as unknown as PostAction;
    const user = userEvent.setup();
    renderEditor(makeConfig(action) as AssistantConfig);
    await user.click(await screen.findByRole("button", { name: /Expand Email the customer settings/ }));

    const checkbox = await screen.findByRole("checkbox", { name: "Send in the customer's language" });
    expect(checkbox).toBeChecked();
    expect(screen.getByText(
      "Translated automatically into the language the customer used. Names, numbers and links stay unchanged.",
    )).toBeInTheDocument();
    await user.click(checkbox);
    expect(checkbox).not.toBeChecked();
  });

  it("does not show customer-language translation for staff email actions", async () => {
    const action: PostAction = {
      kind: "notify_staff",
      key: "staff-email",
      enabled: true,
      trigger: { events: ["conversation_completed"], channels: [], services: [] },
      to: ["team@example.test"],
      subject_template: "New enquiry",
      body_template: "A customer contacted the team.",
    };
    const user = userEvent.setup();
    renderEditor(makeConfig(action) as AssistantConfig);
    await user.click(await screen.findByRole("button", { name: /Expand Notify your team settings/ }));

    expect(screen.queryByRole("checkbox", { name: "Send in the customer's language" })).not.toBeInTheDocument();
  });

  it("confirms before replacing custom text", async () => {
    const action: PostAction = {
      kind: "email_customer",
      key: "customer-email",
      enabled: true,
      trigger: { events: ["conversation_completed"], channels: [], services: [] },
      subject_template: "My custom subject",
      body_template: "My custom body",
      translate_to_customer_language: true,
    };
    const user = userEvent.setup();
    renderEditor(makeConfig(action) as AssistantConfig);
    await user.click(await screen.findByRole("button", { name: /Expand Email the customer settings/ }));

    await user.selectOptions(
      await screen.findByRole("combobox", { name: "Start from a template" }),
      "inquiry_confirmation",
    );

    expect(screen.getByRole("dialog")).toHaveTextContent("Replace the current subject and message?");
    await user.click(screen.getByRole("button", { name: "Replace" }));
    await waitFor(() => {
      expect(screen.getByLabelText("Subject template 1")).toHaveValue(inquiry.subject_template);
    });
  });

  it("shows the preview response in a sandboxed iframe", async () => {
    const action: PostAction = {
      kind: "email_customer",
      key: "customer-email",
      enabled: true,
      trigger: { events: ["conversation_completed"], channels: [], services: [] },
      subject_template: inquiry.subject_template,
      body_template: inquiry.body_template,
      translate_to_customer_language: true,
    };
    const user = userEvent.setup();
    renderEditor(makeConfig(action) as AssistantConfig);
    await user.click(await screen.findByRole("button", { name: /Expand Email the customer settings/ }));

    await user.click(await screen.findByRole("button", { name: "Preview email" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("We've received your enquiry");
    const frame = screen.getByTitle("Email preview");
    expect(frame).toHaveAttribute("sandbox", "");
    expect(frame).toHaveAttribute("srcdoc", expect.stringContaining("<html>"));
  });
});
