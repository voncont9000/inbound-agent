const MAX_KEY_LENGTH = 48;

export function slugify(label: string, fallback: string): string {
  const slug = label
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/ß/g, "ss")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (!slug) return fallback;
  const withLetter = /^[a-z]/.test(slug) ? slug : `${fallback}_${slug}`;
  return withLetter.slice(0, MAX_KEY_LENGTH).replace(/_+$/, "");
}

export function uniqueKey(base: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  if (!used.has(base)) return base;
  let suffix = 2;
  while (used.has(`${base}_${suffix}`)) suffix += 1;
  return `${base}_${suffix}`;
}

export function keyFromLabel(label: string, taken: Iterable<string>, fallback: string): string {
  return uniqueKey(slugify(label, fallback), taken);
}

/**
 * Keys follow the label only while the item has never been saved; once a key is in the
 * saved config it stays stable when the label is edited.
 */
export function followLabel({ key, label, taken, saved, fallback }: {
  key: string;
  label: string;
  taken: Iterable<string>;
  saved: Iterable<string>;
  fallback: string;
}): string {
  if (new Set(saved).has(key)) return key;
  return keyFromLabel(label, [...taken].filter((item) => item !== key), fallback);
}

/** A readable label for a stored key, e.g. `office_email` → `Office email`. */
export function humanizeKey(key: string): string {
  const text = key.replace(/[_-]+/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}
