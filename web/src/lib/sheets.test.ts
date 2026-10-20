import { describe, expect, it } from "vitest";
import { parseGoogleSheetId } from "./sheets";

describe("parseGoogleSheetId", () => {
  it("extracts an ID from a Google Sheets URL", () => {
    expect(parseGoogleSheetId("https://docs.google.com/spreadsheets/d/sheet_123-abc/edit#gid=0")).toBe("sheet_123-abc");
  });

  it("extracts an ID from a URL with a user path", () => {
    expect(parseGoogleSheetId("https://docs.google.com/spreadsheets/u/0/d/sheet_123/edit")).toBe("sheet_123");
  });

  it("accepts a bare spreadsheet ID", () => {
    expect(parseGoogleSheetId("sheet_123-abc")).toBe("sheet_123-abc");
  });

  it("rejects an unparseable link", () => {
    expect(parseGoogleSheetId("https://docs.google.com/document/d/not-a-sheet/edit")).toBeNull();
  });
});
