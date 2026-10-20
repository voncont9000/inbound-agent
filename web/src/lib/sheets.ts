const sheetIdPattern = /^[A-Za-z0-9_-]+$/;
const sheetUrlPattern = /\/spreadsheets\/(?:u\/\d+\/)?d\/([A-Za-z0-9_-]+)/;

export function isGoogleSheetsUrl(value: string): boolean {
  return sheetUrlPattern.test(value.trim());
}

export function parseGoogleSheetId(value: string): string | null {
  const input = value.trim();
  if (!input) return null;
  const urlMatch = sheetUrlPattern.exec(input);
  if (urlMatch) return urlMatch[1];
  return sheetIdPattern.test(input) ? input : null;
}
