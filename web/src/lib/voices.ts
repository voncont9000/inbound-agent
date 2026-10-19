import type { Schemas } from "../api/client";
import type { Locale } from "./config";

type VoiceCard = Schemas["VoiceCard"];

export const RECOMMENDED_VOICE_COUNT = 6;

/** A short, gender-mixed list: the selected voice, the default, then voices for the exact locale. */
export function recommendedVoices(voices: VoiceCard[], language: Locale, selectedId: string, count = RECOMMENDED_VOICE_COUNT): VoiceCard[] {
  const exact = (voice: VoiceCard) => voice.locale.toLowerCase() === language.toLowerCase();
  const pinned = voices.filter((voice) => voice.id === selectedId || voice.is_default);
  const rest = voices.filter((voice) => !pinned.includes(voice));
  const ranked = [...rest.filter(exact), ...rest.filter((voice) => !exact(voice))];
  const byGender = new Map<string, VoiceCard[]>();
  ranked.forEach((voice) => byGender.set(voice.gender, [...(byGender.get(voice.gender) ?? []), voice]));
  const mixed: VoiceCard[] = [];
  const queues = [...byGender.values()];
  while (queues.some((queue) => queue.length)) queues.forEach((queue) => { const next = queue.shift(); if (next) mixed.push(next); });
  const picked = [...pinned, ...mixed].slice(0, count);
  return voices.filter((voice) => picked.includes(voice));
}
