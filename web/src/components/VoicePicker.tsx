import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap, type Schemas } from "../api/client";
import { EmptyState, ErrorState, LoadingState } from "./States";
import type { Locale } from "../lib/config";

type VoiceCatalogue = Schemas["VoiceCatalogueResponse"];
type VoiceCard = Schemas["VoiceCard"];

const queryKey = (locale: Locale) => ["voices", locale] as const;

function getVoiceCatalogue(locale: Locale) {
  return unwrap(
    api.GET("/voices", {
      params: { query: { locale } },
    }),
  );
}

export function VoicePicker({
  language,
  voiceId,
  onChange,
}: {
  language: Locale;
  voiceId: string | null;
  onChange: (next: { language: Locale; voiceId: string | null }) => void;
}) {
  const queryClient = useQueryClient();
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playingVoiceId, setPlayingVoiceId] = useState<string | null>(null);
  const [changingLanguage, setChangingLanguage] = useState(false);
  const voicesQuery = useQuery({
    queryKey: queryKey(language),
    queryFn: () => getVoiceCatalogue(language),
  });

  useEffect(() => {
    audioRef.current?.pause();
    setPlayingVoiceId(null);
  }, [language]);

  const selectLanguage = async (event: ChangeEvent<HTMLSelectElement>) => {
    const nextLanguage = event.currentTarget.value as Locale;
    if (nextLanguage === language) return;
    setChangingLanguage(true);
    try {
      const nextCatalogue: VoiceCatalogue = await queryClient.fetchQuery({
        queryKey: queryKey(nextLanguage),
        queryFn: () => getVoiceCatalogue(nextLanguage),
      });
      const currentVoice = voiceId ?? nextCatalogue.default_voice_id;
      const keepVoice = nextCatalogue.voices.some(({ id }) => id === currentVoice);
      onChange({
        language: nextLanguage,
        voiceId: keepVoice && voiceId !== null ? voiceId : null,
      });
    } catch {
      onChange({ language: nextLanguage, voiceId: null });
    } finally {
      setChangingLanguage(false);
    }
  };

  const selectVoice = (voice: VoiceCard) => {
    const catalogue = voicesQuery.data;
    if (!catalogue) return;
    onChange({
      language,
      voiceId: voice.id === catalogue.default_voice_id ? null : voice.id,
    });
  };

  const togglePreview = async (voice: VoiceCard) => {
    const audio = audioRef.current;
    if (!audio || !voice.preview_url) return;
    if (playingVoiceId === voice.id) {
      audio.pause();
      audio.currentTime = 0;
      setPlayingVoiceId(null);
      return;
    }
    audio.pause();
    audio.src = voice.preview_url;
    audio.load();
    setPlayingVoiceId(voice.id);
    try {
      await audio.play();
    } catch {
      setPlayingVoiceId(null);
    }
  };

  if (voicesQuery.isLoading) return <LoadingState label="Loading voices…" />;
  if (voicesQuery.error) {
    return (
      <ErrorState
        message={
          voicesQuery.error instanceof Error
            ? voicesQuery.error.message
            : "Voices could not be loaded."
        }
      />
    );
  }

  const catalogue = voicesQuery.data;
  if (!catalogue) return <EmptyState>No voices are available.</EmptyState>;
  const selectedVoiceId = voiceId ?? catalogue.default_voice_id;
  const multilingualUnavailable =
    language === "multi" &&
    !catalogue.locales.some(({ code }) => code === "multi");

  return (
    <section className="voice-picker">
      <label className="field-label" htmlFor="field-language">
        Language
      </label>
      <select
        id="field-language"
        value={language}
        onChange={selectLanguage}
        disabled={changingLanguage}
      >
        {multilingualUnavailable && (
          <option value="multi">Multilingual</option>
        )}
        {catalogue.locales.map((locale) => (
          <option key={locale.code} value={locale.code}>
            {locale.label}
          </option>
        ))}
      </select>
      {multilingualUnavailable && (
        <p className="help" role="note">
          Multilingual isn't available with the current voice service, so the
          assistant will speak its default voice language.
        </p>
      )}
      <fieldset className="voice-picker-fieldset" disabled={changingLanguage}>
        <legend>Voice</legend>
        {catalogue.voices.length ? (
          <div className="voice-picker-list" role="radiogroup" aria-label="Voice">
            {catalogue.voices.map((voice) => {
              const selected = voice.id === selectedVoiceId;
              const playing = playingVoiceId === voice.id;
              return (
                <div
                  className={`voice-picker-card${selected ? " voice-picker-card-selected" : ""}`}
                  key={voice.id}
                >
                  <label className="voice-picker-choice">
                    <input
                      type="radio"
                      name="assistant-voice"
                      value={voice.id}
                      checked={selected}
                      onChange={() => selectVoice(voice)}
                    />
                    <span className="voice-picker-copy">
                      <strong>{voice.name}</strong>
                      <span>
                        {voice.accent} · {voice.gender} · {voice.locale}
                      </span>
                    </span>
                  </label>
                  <button
                    type="button"
                    className="voice-picker-preview"
                    disabled={!voice.preview_url}
                    title={voice.preview_url ? undefined : "No preview available"}
                    aria-label={`${playing ? "Stop" : "Play"} preview for ${voice.name}`}
                    onClick={() => void togglePreview(voice)}
                  >
                    {playing ? "Stop" : "Play"}
                  </button>
                </div>
              );
            })}
          </div>
        ) : (
          <EmptyState>No voices are available for this language.</EmptyState>
        )}
      </fieldset>
      {changingLanguage && (
        <p role="status" className="muted">
          Loading voices…
        </p>
      )}
      <audio
        ref={audioRef}
        hidden
        onEnded={() => setPlayingVoiceId(null)}
      />
    </section>
  );
}
