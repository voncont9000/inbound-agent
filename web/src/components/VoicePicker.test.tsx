import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Schemas } from "../api/client";
import type { Locale } from "../lib/config";
import { voiceCatalogueFor } from "../mocks/fixtures";
import { server } from "../mocks/server";
import { VoicePicker } from "./VoicePicker";

function renderPicker(
  onChange: (next: { language: Locale; voiceId: string | null }) => void,
  language: Locale = "en-GB",
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <VoicePicker language={language} voiceId={null} onChange={onChange} />
    </QueryClientProvider>,
  );
}

describe("VoicePicker", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows locale voices and reports a selected voice", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    renderPicker(onChange);

    expect(await screen.findByRole("radio", { name: /Beatrice/i })).toBeChecked();
    const voice = await screen.findByRole("radio", { name: /Olive/i });
    await user.click(voice);

    expect(onChange).toHaveBeenCalledWith({
      language: "en-GB",
      voiceId: "voice-en-GB-1",
    });
  });

  it("omits multilingual when the catalogue does not offer it", async () => {
    const catalogue = voiceCatalogueFor("en-GB");
    catalogue.locales = catalogue.locales.filter(({ code }) => code !== "multi");
    server.use(
      http.get("*/api/voices", () => HttpResponse.json(catalogue)),
    );
    renderPicker(vi.fn());

    expect(await screen.findByRole("radio", { name: /Beatrice/i })).toBeChecked();
    expect(
      screen.queryByRole("option", { name: "Multilingual" }),
    ).not.toBeInTheDocument();
  });

  it("keeps a saved multilingual selection and explains the default voice fallback", async () => {
    const catalogue = voiceCatalogueFor("multi");
    catalogue.locales = catalogue.locales.filter(({ code }) => code !== "multi");
    server.use(
      http.get("*/api/voices", () => HttpResponse.json(catalogue)),
    );
    renderPicker(vi.fn(), "multi");

    const language = await screen.findByLabelText("Language");
    expect(language).toHaveValue("multi");
    expect(screen.getByRole("option", { name: "Multilingual" })).toHaveValue(
      "multi",
    );
    expect(screen.getByRole("note")).toHaveTextContent(
      "Multilingual isn't available with the current voice service, so the assistant will speak its default voice language.",
    );
  });

  it("keeps a voice available in the new locale and resets an unavailable one", async () => {
    const sharedVoice: Schemas["VoiceCard"] = {
      id: "shared-voice",
      name: "Shared Voice",
      gender: "female",
      accent: "British",
      locale: "en-GB",
      preview_url: "https://preview.example/shared.mp3",
      is_default: false,
    };
    server.use(
      http.get("*/api/voices", ({ request }) => {
        const requestedLocale = new URL(request.url).searchParams.get("locale");
        const locale =
          requestedLocale === "de-DE"
            ? "de-DE"
            : requestedLocale === "en-US"
              ? "en-US"
              : "en-GB";
        const response = voiceCatalogueFor(locale);
        if (locale !== "en-US") {
          response.voices.push({
            ...sharedVoice,
            locale,
            accent: locale === "de-DE" ? "German" : "British",
          });
        }
        return HttpResponse.json(response);
      }),
    );
    const onChange = vi.fn();
    const user = userEvent.setup();
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <VoicePicker language="en-GB" voiceId={null} onChange={onChange} />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("radio", { name: /Shared Voice/i }));
    rerender(
      <QueryClientProvider client={queryClient}>
        <VoicePicker language="en-GB" voiceId="shared-voice" onChange={onChange} />
      </QueryClientProvider>,
    );
    await user.selectOptions(screen.getByLabelText("Language"), "de-DE");
    await waitFor(() => {
      expect(onChange).toHaveBeenLastCalledWith({
        language: "de-DE",
        voiceId: "shared-voice",
      });
    });

    rerender(
      <QueryClientProvider
        client={queryClient}
      >
        <VoicePicker
          language="de-DE"
          voiceId="shared-voice"
          onChange={onChange}
        />
      </QueryClientProvider>,
    );
    await screen.findByRole("radio", { name: /Shared Voice/i });
    await user.selectOptions(screen.getByLabelText("Language"), "en-US");
    await waitFor(() => {
      expect(onChange).toHaveBeenLastCalledWith({
        language: "en-US",
        voiceId: null,
      });
    });
  });

  it("plays and stops one shared preview and disables missing previews", async () => {
    const play = vi
      .spyOn(HTMLMediaElement.prototype, "play")
      .mockResolvedValue(undefined);
    const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
    const user = userEvent.setup();
    renderPicker(vi.fn());

    const playButton = await screen.findByRole("button", {
      name: "Play preview for Olive",
    });
    await user.click(playButton);
    expect(play).toHaveBeenCalledOnce();

    await user.click(
      screen.getByRole("button", { name: "Stop preview for Olive" }),
    );
    expect(pause).toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Play preview for Beatrice" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Play preview for Beatrice" }),
    ).toHaveAttribute("title", "No preview available");
  });

  it("shows a useful error when the catalogue request fails", async () => {
    server.use(
      http.get("*/api/voices", () =>
        HttpResponse.json({ detail: "Voice catalogue unavailable" }, { status: 503 }),
      ),
    );
    renderPicker(vi.fn());

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Voice catalogue unavailable",
    );
  });
});
