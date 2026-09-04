"use client";

/**
 * Local desktop defaults. Clipboard background poll is OFF unless the user
 * turns it on here (CLIPDROP_CONTRACT §3).
 */

import { useEffect, useState } from "react";
import { OPENVAULT_API } from "@/lib/config";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";
import { Switch } from "@/components/ui/Switch";

const CLIPBOARD_PREF_KEY = "openvault.clipboard_watch";

export default function SettingsPage() {
  const isDesktop = Boolean(
    typeof window !== "undefined" && window.openvault?.isDesktop,
  );
  const [watchClipboard, setWatchClipboard] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        if (window.openvault?.getClipboardWatch) {
          const { enabled } = await window.openvault.getClipboardWatch();
          if (!cancelled) setWatchClipboard(enabled);
        } else {
          const raw = localStorage.getItem(CLIPBOARD_PREF_KEY);
          if (!cancelled) setWatchClipboard(raw === "1");
        }
      } catch {
        /* ignore */
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function onToggle(next: boolean) {
    setWatchClipboard(next);
    try {
      localStorage.setItem(CLIPBOARD_PREF_KEY, next ? "1" : "0");
      if (window.openvault?.setClipboardWatch) {
        await window.openvault.setClipboardWatch(next);
      }
    } catch {
      setWatchClipboard(!next);
    }
  }

  return (
    <PageContainer>
      <PageHeader
        title="Settings"
        description="Local desktop defaults. Theme skins live in the top bar picker."
      />
      <div data-glass className="space-y-4 rounded-2xl border border-border bg-card p-5 text-sm">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="font-medium text-foreground">Watch clipboard for API keys</p>
            <p className="mt-1 text-muted-foreground">
              {isDesktop
                ? "When on, the desktop shell notices credential-shaped copies even if OpenVault is in the background. Off by default."
                : "Desktop shell only. In the browser, paste or return to the Vault tab after copying."}
            </p>
          </div>
          <Switch
            checked={watchClipboard}
            onChange={(next) => void onToggle(next)}
            disabled={!ready || !isDesktop}
            ariaLabel="Watch clipboard for API keys"
          />
        </div>

        <hr className="border-border" />

        <p>
          OpenVault API: <code className="text-foreground">{OPENVAULT_API}</code>
        </p>
        <p className="text-muted-foreground">
          Apps get a gateway, not a key — point clients at{" "}
          <code className="text-foreground">http://127.0.0.1:5000/v1</code>.
        </p>
        <p className="text-muted-foreground">
          Topology: Electron → FastAPI <code>:5000</code> + Next <code>:3010</code>.
        </p>
      </div>
    </PageContainer>
  );
}
