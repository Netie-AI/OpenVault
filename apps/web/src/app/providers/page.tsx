"use client";

import { useEffect, useState } from "react";
import { apiFetch, isApiError } from "@/lib/api/client";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";
import { Button } from "@/components/ui/button";

type ProviderRow = {
  id?: string;
  name?: string;
  tier?: string;
  free_notes?: string;
  chat_models?: string[];
  context_window?: number;
  register_url?: string;
  [key: string]: unknown;
};

type Catalog = {
  providers?: ProviderRow[];
  priced?: boolean;
  nvidia_classifier?: boolean;
  count?: number;
  [key: string]: unknown;
};

/** Native provider catalog — replaces the dead OmniRoute iframe. */
export default function ProvidersPage() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [err, setErr] = useState("");

  async function load() {
    setErr("");
    try {
      const data = await apiFetch<Catalog>("/api/providers/catalog");
      setCatalog(data);
    } catch (e) {
      setErr(isApiError(e) ? e.message : String(e));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const providers = catalog?.providers || [];
  const priced = Boolean(catalog?.priced);
  const nvidia = Boolean(catalog?.nvidia_classifier);

  return (
    <PageContainer>
      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <PageHeader
          title="Providers"
          description="Honest FreeRoute catalog. Pinned model ids only. Cost unknown. Not an OmniRoute iframe."
        />
        <Button variant="outline" size="sm" onClick={() => void load()}>
          Refresh
        </Button>
      </div>

      {err ? <p className="mb-4 text-sm text-destructive">{err}</p> : null}

      <div
        data-glass
        className="mb-5 rounded-2xl border border-border bg-card p-4 text-sm"
      >
        <p className="font-medium text-foreground">
          {providers.length} providers. Priced {priced ? "true" : "false"}. NVIDIA
          classifier {nvidia ? "on" : "off"}.
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          No USD. context_window 0 means unknown, not a guessed limit. Unlisted
          model ids fall back. HT2 (live spend of a vaulted key) stays human.
        </p>
      </div>

      {providers.length ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {providers.map((p, i) => {
            const models = Array.isArray(p.chat_models) ? p.chat_models.slice(0, 4) : [];
            const windowTokens = Number(p.context_window || 0);
            return (
              <div
                key={String(p.id || p.name || i)}
                data-glass
                className="rounded-2xl border border-border bg-card p-4"
              >
                <h3 className="text-sm font-semibold text-foreground">
                  {String(p.name || p.id || "provider")}
                </h3>
                <p className="mt-1 font-mono text-xs text-muted-foreground">
                  {String(p.id || "—")} · {String(p.tier || "unknown")}
                </p>
                {models.length ? (
                  <p className="mt-2 font-mono text-[11px] text-muted-foreground">
                    {models.join(", ")}
                    {(p.chat_models?.length || 0) > 4 ? "…" : ""}
                  </p>
                ) : (
                  <p className="mt-2 text-[11px] text-muted-foreground">No pinned chat models.</p>
                )}
                <p className="mt-2 text-[11px] text-muted-foreground">
                  Context {windowTokens > 0 ? `${windowTokens} tokens` : "unknown"} · cost unknown
                </p>
                {p.free_notes ? (
                  <p className="mt-2 text-xs text-muted-foreground">{p.free_notes}</p>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : (
        <pre
          data-glass
          className="overflow-auto rounded-2xl border border-border bg-card p-4 text-xs text-muted-foreground"
        >
          {catalog ? JSON.stringify(catalog, null, 2) : "Loading…"}
        </pre>
      )}
    </PageContainer>
  );
}
