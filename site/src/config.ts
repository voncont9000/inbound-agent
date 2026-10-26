export const APP_URL = (import.meta.env.VITE_APP_URL ?? "https://agent.vinci-labs.tech").replace(/\/$/, "");
export const SITE_URL = "https://yelvo.vinci-labs.tech";
export const DEMO_MAILTO = "mailto:opitz.vincent@gmail.com?subject=Yelvo%20demo";

export function appLink(path: string): string {
  return `${APP_URL}/${path.replace(/^\/+/, "")}`;
}
