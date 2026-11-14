import { expect, it, vi } from "vitest";
import { clearAuthReturnTo, readAuthReturnTo, rememberAuthReturnTo, safeAuthReturnTo } from "./auth-navigation";

it.each(["/", "/chat", "/documents", "/graph", "/models", "/settings", "/setup", "/documents?view=all"])("allows local application destination %s", (path) => {
  expect(safeAuthReturnTo(path)).toBe(path);
});

it.each([null, "", "https://evil.example/", "//evil.example", "/\\evil.example", "/auth/login", "/auth/callback", "/connect?token=private", "/%2F%2Fevil.example", "/chat#token=private"])("rejects an unsafe or looping destination %s", (path) => {
  expect(safeAuthReturnTo(path)).toBe("/");
});

it("keeps the requested destination across an OAuth redirect and clears it after success", () => {
  rememberAuthReturnTo("/documents");
  expect(readAuthReturnTo()).toBe("/documents");
  clearAuthReturnTo();
  expect(readAuthReturnTo()).toBe("/");
});

it("does not fail sign-in if browser session storage is blocked", () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Storage unavailable"); });
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("Storage unavailable"); });
  vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new Error("Storage unavailable"); });
  expect(() => rememberAuthReturnTo("/documents")).not.toThrow();
  expect(readAuthReturnTo()).toBe("/");
  expect(() => clearAuthReturnTo()).not.toThrow();
});
