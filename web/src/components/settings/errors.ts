export function invalidProps(errors: Record<string, string>, path: string) {
  return {
    "aria-invalid": Boolean(errors[path]),
    "aria-describedby": errors[path] ? `field-${path}-error` : undefined,
  };
}

export function hasErrorUnder(errors: Record<string, string>, prefixes: string[]) {
  return Object.keys(errors).some((path) => prefixes.some((prefix) => path === prefix || path.startsWith(`${prefix}.`)));
}
