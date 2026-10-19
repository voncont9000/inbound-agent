export function formatDate(value?: string | null): string {
  return value
    ? new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value))
    : "—";
}
