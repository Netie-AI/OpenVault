"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

/**
 * Five skins plus "system". `light` and `dim`/`dark` come from OpenShip's token
 * file; `glass` and `ink` are ours, layered on top of the same `--th-*` names,
 * which is why every component keeps consuming `bg-card` / `border-border` and
 * inherits a skin for free.
 */
export type Theme = "light" | "dim" | "dark" | "glass" | "ink" | "system";
export type ResolvedTheme = Exclude<Theme, "system">;

/** Skins in picker order. `system` is last because it is a mode, not a look. */
export const THEMES: ReadonlyArray<{ id: Theme; label: string; hint: string }> = [
  { id: "light", label: "Light", hint: "Paper white" },
  { id: "dim", label: "Dim", hint: "Soft dark" },
  { id: "dark", label: "Dark", hint: "True black" },
  { id: "glass", label: "Glass", hint: "Blurred translucent surfaces" },
  { id: "ink", label: "Ink", hint: "High contrast" },
  { id: "system", label: "System", hint: "Follow the OS" },
];

const STORAGE_KEY = "theme";
const RESOLVED: ReadonlyArray<ResolvedTheme> = ["light", "dim", "dark", "glass", "ink"];

/** Skins whose surfaces are dark — decides the `color-scheme` hint. */
const DARK_SKINS: ReadonlyArray<ResolvedTheme> = ["dim", "dark", "glass", "ink"];

interface ThemeContextValue {
  /** What the user chose, which may be "system". */
  theme: Theme;
  /** What is actually painted right now. */
  resolvedTheme: ResolvedTheme;
  setTheme: (t: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: "light",
  resolvedTheme: "light",
  setTheme: () => {},
});

export function useTheme() {
  return useContext(ThemeContext);
}

function isDesktopApp(): boolean {
  return (
    typeof window !== "undefined" &&
    !!(window as { openvault?: { isDesktop?: boolean } }).openvault?.isDesktop
  );
}

function isTheme(value: string | null): value is Theme {
  return value === "system" || RESOLVED.includes(value as ResolvedTheme);
}

function resolveTheme(t: Theme): ResolvedTheme {
  if (t !== "system") return t;
  if (typeof window === "undefined") return "light";
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  if (!dark) return "light";
  // The desktop app's default dark appearance is the softer "dim"; served over
  // the network it keeps full "dark".
  return isDesktopApp() ? "dim" : "dark";
}

function applyTheme(resolved: ResolvedTheme) {
  const root = document.documentElement;
  root.setAttribute("data-theme", resolved);
  // Without this the native scrollbars and form controls stay light on a dark
  // skin, which reads as a rendering bug.
  root.style.colorScheme = DARK_SKINS.includes(resolved) ? "dark" : "light";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("light");
  const [resolvedTheme, setResolved] = useState<ResolvedTheme>("light");

  // Initialise from localStorage. An explicit stored choice always wins. With
  // no stored preference the desktop app follows the OS — it is a native window
  // and users expect it to respect their appearance setting.
  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    const t: Theme = isTheme(stored) ? stored : isDesktopApp() ? "system" : "light";
    const resolved = resolveTheme(t);
    setThemeState(t);
    setResolved(resolved);
    applyTheme(resolved);
  }, []);

  // Follow OS changes only while in system mode.
  useEffect(() => {
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      const r = resolveTheme("system");
      setResolved(r);
      applyTheme(r);
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = useCallback((t: Theme) => {
    const resolved = resolveTheme(t);
    setThemeState(t);
    setResolved(resolved);
    localStorage.setItem(STORAGE_KEY, t);
    applyTheme(resolved);
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, resolvedTheme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

/**
 * Inline script for <head>. It runs before hydration and before first paint, so
 * the correct `data-theme` is already on <html> when the first frame is drawn.
 * Dropping this causes a white flash on every launch — the app is a desktop
 * window that opens cold, so the flash is unmissable.
 *
 * Keep it dependency-free and synchronous. Any throw falls back to light.
 */
export function ThemeScript() {
  const script = `
    (function(){
      try {
        var t = localStorage.getItem('${STORAGE_KEY}');
        // window.openvault is injected by the Electron preload before this runs.
        var isDesktop = !!(window.openvault && window.openvault.isDesktop);
        var sysDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
        var skins = ${JSON.stringify(RESOLVED)};
        var resolved;
        if (skins.indexOf(t) !== -1) resolved = t;
        else if (t === 'system') resolved = sysDark ? (isDesktop ? 'dim' : 'dark') : 'light';
        // No stored pref: desktop follows the OS (dark -> dim), web stays light.
        else resolved = (isDesktop && sysDark) ? 'dim' : 'light';
        document.documentElement.setAttribute('data-theme', resolved);
        document.documentElement.style.colorScheme =
          ${JSON.stringify(DARK_SKINS)}.indexOf(resolved) !== -1 ? 'dark' : 'light';
      } catch (e) {
        document.documentElement.setAttribute('data-theme', 'light');
      }
    })();
  `;
  return <script dangerouslySetInnerHTML={{ __html: script }} />;
}
