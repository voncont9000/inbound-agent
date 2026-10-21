import { useEffect, useId, useMemo, useRef, useState, type ChangeEvent, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap, type Schemas } from "../api/client";
import { EmptyState, ErrorState, LoadingState } from "./States";
import { Field } from "./settings/fields";
import type { Locale } from "../lib/config";
import { RECOMMENDED_VOICE_COUNT, recommendedVoices } from "../lib/voices";

type VoiceCatalogue = Schemas["VoiceCatalogueResponse"];
type VoiceCard = Schemas["VoiceCard"];

const queryKey = (locale: Locale) => ["voices", locale] as const;

function uniqueValues(voices: VoiceCard[], key: "gender" | "accent") {
  return [...new Set(voices.map((voice) => voice[key]).filter(Boolean))].sort();
}

function capitalize(value: string) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

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
  languageNotice,
}: {
  language: Locale;
  voiceId: string | null;
  onChange: (next: { language: Locale; voiceId: string | null }) => void;
  languageNotice?: (languageLabel: string) => ReactNode;
}) {
  const queryClient = useQueryClient();
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const selectedVoiceRef = useRef<HTMLInputElement | null>(null);
  const initialVoiceScrolled = useRef(false);
  const [playingVoiceId, setPlayingVoiceId] = useState<string | null>(null);
  const [changingLanguage, setChangingLanguage] = useState(false);
  const [search, setSearch] = useState("");
  const [gender, setGender] = useState("");
  const [accent, setAccent] = useState("");
  const [showAll, setShowAll] = useState(false);
  const listId = useId();
  const voicesQuery = useQuery({
    queryKey: queryKey(language),
    queryFn: () => getVoiceCatalogue(language),
  });

  useEffect(() => {
    audioRef.current?.pause();
    setPlayingVoiceId(null);
    setAccent("");
  }, [language]);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = 0;
  }, [search, gender, accent, showAll]);

  const allVoices = useMemo(() => voicesQuery.data?.voices ?? [], [voicesQuery.data]);
  const selectedVoiceId = voiceId ?? voicesQuery.data?.default_voice_id ?? "";
  const query = search.trim().toLowerCase();
  const filtering = Boolean(query || gender || accent);
  const visibleVoices = useMemo(() => {
    if (!filtering) return showAll ? allVoices : recommendedVoices(allVoices, language, selectedVoiceId);
    return allVoices.filter((voice) =>
      (!query || `${voice.name} ${voice.accent} ${voice.locale}`.toLowerCase().includes(query)) &&
      (!gender || voice.gender === gender) &&
      (!accent || voice.accent === accent));
  }, [allVoices, filtering, showAll, language, selectedVoiceId, query, gender, accent]);

  useEffect(() => {
    const list = listRef.current;
    const selected = selectedVoiceRef.current;
    if (
      initialVoiceScrolled.current ||
      !list ||
      !selected ||
      typeof selected.scrollIntoView !== "function"
    ) return;
    selected.scrollIntoView({ block: "nearest" });
    initialVoiceScrolled.current = true;
  }, [visibleVoices, selectedVoiceId]);

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
  const genders = uniqueValues(catalogue.voices, "gender");
  const accents = uniqueValues(catalogue.voices, "accent");
  const canExpand = !filtering && catalogue.voices.length > RECOMMENDED_VOICE_COUNT;
  const multilingualUnavailable =
    language === "multi" &&
    !catalogue.locales.some(({ code }) => code === "multi");

  return (
    <section className="voice-picker">
      <Field path="language" label="Language" layout="row">{(fieldId) => <select
        id={fieldId}
        value={language}
        onChange={selectLanguage}
        disabled={changingLanguage}
      >
        {multilingualUnavailable && <option value="multi">Multilingual</option>}
        {catalogue.locales.map((locale) => (
          <option key={locale.code} value={locale.code}>{locale.label}</option>
        ))}
      </select>}</Field>
      {languageNotice?.(
        catalogue.locales.find(({ code }) => code === language)?.label ?? language,
      )}
      {multilingualUnavailable && (
        <p className="help" role="note">
          Multilingual isn't available with the current voice service, so the
          assistant will speak its default voice language.
        </p>
      )}
      <fieldset className="voice-picker-fieldset" disabled={changingLanguage}>
        <legend>Voice</legend>
        {catalogue.voices.length > RECOMMENDED_VOICE_COUNT && <div className="voice-picker-filters">
          <input
            type="search"
            aria-label="Search voices"
            placeholder="Search by name or accent"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          {genders.length > 1 && <select aria-label="Gender" value={gender} onChange={(event) => setGender(event.target.value)}>
            <option value="">Any gender</option>
            {genders.map((item) => <option key={item} value={item}>{capitalize(item)}</option>)}
          </select>}
          {accents.length > 1 && <select aria-label="Accent" value={accent} onChange={(event) => setAccent(event.target.value)}>
            <option value="">Any accent</option>
            {accents.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>}
        </div>}
        {catalogue.voices.length ? (
          <div ref={listRef} className="voice-picker-list" role="radiogroup" aria-label="Voice" id={listId}>
            {visibleVoices.map((voice) => {
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
                      ref={selected ? selectedVoiceRef : undefined}
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
            {!visibleVoices.length && <p className="muted">No voices match these filters.</p>}
          </div>
        ) : (
          <EmptyState>No voices are available for this language.</EmptyState>
        )}
        <p className="help voice-picker-count">
          {filtering
            ? `${visibleVoices.length} of ${catalogue.voices.length} voices match.`
            : canExpand && !showAll
              ? `Recommended voices for this language (${visibleVoices.length} of ${catalogue.voices.length}).`
              : ""}
          {canExpand && <button type="button" className="button-link" aria-expanded={showAll} aria-controls={listId} onClick={() => setShowAll(!showAll)}>
            {showAll ? "Show recommended voices" : `Show all ${catalogue.voices.length} voices`}
          </button>}
        </p>
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
