/**
 * Remember which provider signup the user just opened.
 *
 * Flow: Vault shows "Get free key" → we stash the provider → user copies a key
 * from the signup tab → ClipDrop prefers that provider even when the key shape
 * is weak. We never scrape the signup page; we only remember the click.
 */

const STORAGE_KEY = "openvault.register_intent";

export interface RegisterIntent {
  providerId: string;
  providerName: string;
  registerUrl: string;
  clickedAt: number;
}

/** Intents older than this are ignored — the user probably abandoned signup. */
const TTL_MS = 2 * 60 * 60 * 1000;

export function rememberRegisterIntent(intent: Omit<RegisterIntent, "clickedAt">): void {
  if (typeof window === "undefined") return;
  const full: RegisterIntent = { ...intent, clickedAt: Date.now() };
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(full));
  } catch {
    /* private mode / quota — skip memory, detection still works */
  }
}

export function readRegisterIntent(): RegisterIntent | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as RegisterIntent;
    if (!parsed?.providerId || !parsed?.clickedAt) return null;
    if (Date.now() - parsed.clickedAt > TTL_MS) {
      sessionStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function clearRegisterIntent(): void {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

/** Short relative time for register-return copy ("a minute ago"). */
export function formatRegisterAgo(clickedAt: number, now: number = Date.now()): string {
  const sec = Math.max(0, Math.floor((now - clickedAt) / 1000));
  if (sec < 45) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 2) return "a minute ago";
  if (min < 60) return `${min} minutes ago`;
  const hr = Math.floor(min / 60);
  if (hr < 2) return "an hour ago";
  return `${hr} hours ago`;
}
