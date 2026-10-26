export function scoreTone(overall: number | null | undefined): "neutral" | "success" | "warning" | "danger" {
  if (overall == null) return "neutral";
  if (overall >= 4) return "success";
  if (overall === 3) return "warning";
  return "danger";
}
