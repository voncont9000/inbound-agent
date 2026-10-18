function valueAt(data: unknown, path: string[]): unknown {
  let value = data;
  for (const part of path) {
    value = value && typeof value === "object" && !Array.isArray(value)
      ? Reflect.get(value, part)
      : undefined;
  }
  return value;
}

export function enumAt(data: unknown, path: string[], fallback: string[]): string[] {
  const value = valueAt(data, path);
  return Array.isArray(value) && value.every((item) => typeof item === "string") ? value : fallback;
}

export function rangeAt(data: unknown): { min: number; max: number } {
  const minimum = valueAt(data, ["properties", "review_confidence_threshold", "minimum"]);
  const maximum = valueAt(data, ["properties", "review_confidence_threshold", "maximum"]);
  return {
    min: typeof minimum === "number" ? minimum : 0,
    max: typeof maximum === "number" ? maximum : 1,
  };
}
