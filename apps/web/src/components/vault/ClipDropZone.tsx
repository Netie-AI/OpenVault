"use client";

/**
 * ClipDrop — paste/drop front door for the vault.
 *
 * Detect is automatic; store is one deliberate tap (CLIPDROP_CONTRACT).
 * Motion exists to show causality: pulse = we saw it; chip = identity known.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { inferProvider, looksLikeApiSecret } from "@/lib/vault/inferProvider";

interface Props {
  onSecret: (secret: string) => void;
  hero?: boolean;
  ignoreSecret?: string | null;
  /** Catalog ids so the chip can name the provider. */
  knownProviderIds?: ReadonlySet<string>;
  providerNames?: ReadonlyMap<string, string>;
}

declare global {
  interface Window {
    openvault?: {
      isDesktop?: boolean;
      onClipboardSecret?: (cb: (secret: string) => void) => () => void;
    };
  }
}

export function ClipDropZone({
  onSecret,
  hero = false,
  ignoreSecret = null,
  knownProviderIds,
  providerNames,
}: Props) {
  const [hint, setHint] = useState("Paste or drop an API key");
  const [chip, setChip] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [pulsing, setPulsing] = useState(false);
  const lastOffered = useRef<string | null>(null);
  const pulseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const pulseOnce = useCallback(() => {
    setPulsing(true);
    if (pulseTimer.current) clearTimeout(pulseTimer.current);
    pulseTimer.current = setTimeout(() => setPulsing(false), 180);
  }, []);

  const offer = useCallback(
    (raw: string) => {
      const secret = raw.trim();
      if (!looksLikeApiSecret(secret)) return false;
      if (secret === ignoreSecret || secret === lastOffered.current) return false;
      lastOffered.current = secret;

      const guess = inferProvider(secret, knownProviderIds);
      const name =
        (guess.providerId && providerNames?.get(guess.providerId)) ||
        (guess.providerId
          ? guess.providerId.charAt(0).toUpperCase() + guess.providerId.slice(1)
          : null);
      // Name the provider, not the action (CLIPDROP_CONTRACT §4).
      setChip(name ? `${name} key detected` : "API key detected");
      setHint(name ? `${name} key detected` : "API key detected");
      pulseOnce();
      onSecret(secret);
      return true;
    },
    [ignoreSecret, knownProviderIds, onSecret, providerNames, pulseOnce],
  );

  useEffect(() => {
    function onPaste(e: ClipboardEvent) {
      const target = e.target as HTMLElement | null;
      if (target?.closest("input, textarea, [contenteditable=true]")) return;
      const text = e.clipboardData?.getData("text") ?? "";
      if (offer(text)) e.preventDefault();
    }
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [offer]);

  // Focused-tab clipboard read only — background poll is Electron + Settings.
  useEffect(() => {
    async function checkClipboard() {
      try {
        if (!navigator.clipboard?.readText) return;
        if (document.visibilityState !== "visible") return;
        const text = await navigator.clipboard.readText();
        offer(text);
      } catch {
        /* NotAllowedError — ignore */
      }
    }
    function onFocus() {
      void checkClipboard();
    }
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [offer]);

  useEffect(() => {
    const unsub = window.openvault?.onClipboardSecret?.((secret) => {
      offer(secret);
    });
    return () => {
      unsub?.();
    };
  }, [offer]);

  useEffect(
    () => () => {
      if (pulseTimer.current) clearTimeout(pulseTimer.current);
    },
    [],
  );

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const text =
      e.dataTransfer.getData("text/plain") || e.dataTransfer.getData("text") || "";
    if (offer(text)) return;
    setHint("That doesn't look like an API key");
    setChip(null);
  }

  return (
    <div
      role="region"
      aria-label="Paste or drop API key"
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
      data-glass
      className={
        hero
          ? `ov-clipdrop rounded-2xl border-2 border-dashed px-6 py-14 text-center ${
              dragOver ? "border-foreground bg-accent/40" : "border-border bg-card"
            }`
          : `ov-clipdrop rounded-xl border border-dashed px-4 py-4 text-sm ${
              dragOver ? "border-foreground bg-accent/40" : "border-border bg-card/60"
            }`
      }
      data-pulse={pulsing ? "1" : undefined}
    >
      <p className={hero ? "text-base font-medium text-foreground" : "font-medium text-foreground"}>
        {hero ? "Copy a key. We catch it." : "ClipDrop"}
      </p>
      {chip ? (
        <p
          key={chip}
          className="ov-chip-in mx-auto mt-2 inline-block rounded-full border border-border px-2.5 py-0.5 text-xs text-foreground"
        >
          {chip}
        </p>
      ) : (
        <p
          className={
            hero
              ? "mx-auto mt-2 max-w-md text-sm text-muted-foreground"
              : "mt-1 text-xs text-muted-foreground"
          }
        >
          {hint}
        </p>
      )}
      {hero && !chip ? (
        <p className="mt-4 text-xs text-muted-foreground">
          Paste here or drop a key — we ask once before storing.
        </p>
      ) : null}
    </div>
  );
}
