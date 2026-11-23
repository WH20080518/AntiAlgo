import type { UserPrefs } from "../types";

const READ_IDS_STORAGE_KEY = "anti-algo-hub:read-ids:v1";
const PREFS_STORAGE_KEY = "anti-algo-hub:prefs:v1";
const MASTERED_TERMS_STORAGE_KEY = "anti-algo-hub:mastered-terms:v1";

const DEFAULT_PREFS: UserPrefs = {
  antiSpoiler: true,
  hideRead: false,
};

export function loadReadIds(): Set<string> {
  try {
    const raw = window.localStorage.getItem(READ_IDS_STORAGE_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((id): id is string => typeof id === "string"));
  } catch {
    return new Set();
  }
}

export function saveReadIds(readIds: Set<string>): void {
  try {
    window.localStorage.setItem(
      READ_IDS_STORAGE_KEY,
      JSON.stringify(Array.from(readIds))
    );
  } catch {
    // Ignore storage quota / privacy mode failures cleanly
  }
}

export function loadPrefs(): UserPrefs {
  try {
    const raw = window.localStorage.getItem(PREFS_STORAGE_KEY);
    if (!raw) return DEFAULT_PREFS;
    const parsed = JSON.parse(raw) as Partial<UserPrefs>;
    return {
      antiSpoiler:
        typeof parsed.antiSpoiler === "boolean"
          ? parsed.antiSpoiler
          : DEFAULT_PREFS.antiSpoiler,
      hideRead:
        typeof parsed.hideRead === "boolean"
          ? parsed.hideRead
          : DEFAULT_PREFS.hideRead,
    };
  } catch {
    return DEFAULT_PREFS;
  }
}

export function savePrefs(prefs: UserPrefs): void {
  try {
    window.localStorage.setItem(PREFS_STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // Ignore storage quota / privacy mode failures cleanly
  }
}

export function loadMasteredTermKeys(): Record<string, boolean> {
  try {
    const raw = window.localStorage.getItem(MASTERED_TERMS_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {};
    }
    const clean: Record<string, boolean> = {};
    for (const [k, v] of Object.entries(parsed)) {
      if (typeof v === "boolean") {
        clean[k] = v;
      }
    }
    return clean;
  } catch {
    return {};
  }
}

export function saveMasteredTermKeys(map: Record<string, boolean>): void {
  try {
    window.localStorage.setItem(
      MASTERED_TERMS_STORAGE_KEY,
      JSON.stringify(map)
    );
  } catch {
    // Ignore storage quota / privacy mode failures cleanly
  }
}
