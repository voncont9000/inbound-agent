import { appLink } from "./config";

export function signupUrl(website: string): string {
  const value = website.trim();
  return value ? `${appLink("/signup")}?website=${encodeURIComponent(value)}` : appLink("/signup");
}
