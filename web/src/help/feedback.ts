export const feedbackStorageKey = "inbound.help.feedback";

export type HelpFeedback = "yes" | "no";
type FeedbackStore = Record<string, HelpFeedback>;

export function readHelpFeedback(): FeedbackStore {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(feedbackStorageKey) ?? "{}");
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).filter((entry): entry is [string, HelpFeedback] =>
      entry[1] === "yes" || entry[1] === "no"));
  } catch {
    return {};
  }
}

export function saveHelpFeedback(articlePath: string, helpful: HelpFeedback): void {
  const feedback = readHelpFeedback();
  feedback[articlePath] = helpful;
  localStorage.setItem(feedbackStorageKey, JSON.stringify(feedback));
}
