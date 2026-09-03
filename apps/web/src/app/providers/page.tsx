"use client";

import { useEffect, useState } from "react";
import { apiFetch, isApiError } from "@/lib/api/client";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";
import { Button } from "@/components/ui/button";

type Catalog = {
  providers?: Array<{ id?: string; name?: string; [key: string]: unknown }>;
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

  return (
    <PageContainer>
      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <PageHeader
          title="Providers"
          description="Catalog from OpenMW — not an OmniRoute iframe."
        />
        <Button variant="outline" size="sm" onClick={() => void load()}>
          Refresh
        </Button>
      </div>

      {err ? <p className="mb-4 text-sm text-destructive">{err}</p> : null}

      {providers.length ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {providers.map((p, i) => (
            <div
              key={String(p.id || p.name || i)}
              data-glass
              className="rounded-2xl border border-border bg-card p-4"
            >
              <h3 className="text-sm font-semibold text-foreground">
                {String(p.name || p.id || "provider")}
              </h3>
              <p className="mt-1 font-mono text-xs text-muted-foreground">
                {String(p.id || "—")}
              </p>
            </div>
          ))}
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
