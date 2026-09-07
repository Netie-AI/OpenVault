"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch, isApiError } from "@/lib/api/client";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";
import { Button } from "@/components/ui/button";

type Provider = {
  id?: string;
  name?: string;
  tier?: string;
  free_notes?: string;
  spendable?: boolean;
  chat_models?: string[];
  register_url?: string;
  openai_compatible?: boolean;
};

type Catalog = {
  providers?: Provider[];
  count?: number;
};

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
          description="Curated FreeRoute catalog -- OpenAI-compat hops that resolve model=auto. Not an OmniRoute iframe."
        />
        <div className="flex gap-2">
          <Button asChild variant="outline" size="sm">
            <Link href="/tool/register">Register</Link>
          </Button>
          <Button variant="outline" size="sm" onClick={() => void load()}>
            Refresh
          </Button>
        </div>
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
              <div className="flex items-start justify-between gap-2">
                <h3 className="text-sm font-semibold text-foreground">
                  {String(p.name || p.id || "provider")}
                </h3>
                <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                  {p.tier || "?"}
                </span>
              </div>
              <p className="mt-1 font-mono text-xs text-muted-foreground">
                {String(p.id || "--")}
              </p>
              {p.free_notes ? (
                <p className="mt-2 text-xs text-muted-foreground">{p.free_notes}</p>
              ) : null}
              <p className="mt-2 text-xs text-muted-foreground">
                {p.spendable
                  ? `spendable · ${p.chat_models?.[0] || "catalogued"}`
                  : "not a /v1 auto hop"}
              </p>
              {p.id ? (
                <Button asChild className="mt-3" size="sm" variant="outline">
                  <Link href={`/tool/register?provider=${encodeURIComponent(p.id)}`}>
                    Register
                  </Link>
                </Button>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">{catalog ? "Empty catalog." : "Loading..."}</p>
      )}
    </PageContainer>
  );
}
