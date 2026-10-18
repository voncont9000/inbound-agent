export type ConfigDiff = {
  path: string;
  kind: "added" | "removed" | "changed";
  oldValue?: string;
  newValue?: string;
};

function flatten(value: unknown, prefix = "", result: Record<string, string> = {}): Record<string, string> {
  if (Array.isArray(value)) {
    value.forEach((item, index) => flatten(item, prefix ? `${prefix}.${index}` : String(index), result));
  } else if (value && typeof value === "object") {
    Object.entries(value).forEach(([key, item]) =>
      flatten(item, prefix ? `${prefix}.${key}` : key, result),
    );
  } else {
    result[prefix] = value === undefined ? "—" : JSON.stringify(value);
  }
  return result;
}

export function diffConfigs(oldConfig: unknown, newConfig: unknown): ConfigDiff[] {
  const before = flatten(oldConfig);
  const after = flatten(newConfig);
  const changes: ConfigDiff[] = [];
  for (const path of [...new Set([...Object.keys(before), ...Object.keys(after)])].sort()) {
    if (!(path in before)) changes.push({ path, kind: "added", newValue: after[path] });
    else if (!(path in after)) changes.push({ path, kind: "removed", oldValue: before[path] });
    else if (before[path] !== after[path]) changes.push({ path, kind: "changed", oldValue: before[path], newValue: after[path] });
  }
  return changes;
}
