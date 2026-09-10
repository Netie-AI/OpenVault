"use client";

/**
 * /tool/register -- in-app account registration deep-link.
 *
 * Not the Marketing netie.ai /rates page. Remembers which provider the user
 * clicked, opens the catalog register_url, then sends them to Keys to install.
 */

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";
import { apiGet, isApiError } from "@/lib/api/client";
import { rememberRegisterIntent } from "@/lib/vault/registerIntent";

type RegisterRow = {
  provider: string;
  name: string;
  tier?: string;
  register_url: string;
  docs_url: string;
  free_notes: string;
  deep_link: string;
  return_path: string;
  spendable: boolean;
  chat_models: string[];
};

type CatalogResponse = { providers?: RegisterRow[] };
type OneResponse = RegisterRow & { ok?: boolean };

function RegisterInner() {
  const params = useSearchParams();
  const provider = (params.get("provider") || "").trim();
  const [row, setRow] = useState<RegisterRow | null>(null);
  const [catalog, setCatalog] = useState<RegisterRow[]>([]);
  const [err, setErr] = useState("");
  const [opened, setOpened] = useState(false);

  useEffect(() => {
    const ac = new AbortController();
    setErr("");
    if (!provider) {
      apiGet<CatalogResponse>("/api/tool/register", { signal: ac.signal })
        .then((data) => setCatalog(data.providers ?? []))
        .catch((e) => setErr(isApiError(e) ? e.message : String(e)));
      return () => ac.abort();
    }
    apiGet<OneResponse>("/api/tool/register", {
      signal: ac.signal,
      query: { provider },
    })
      .then((data) => {
        setRow(data);
        rememberRegisterIntent({
          providerId: data.provider,
          providerName: data.name,
          registerUrl: data.register_url,
        });
      })
      .catch((e) => setErr(isApiError(e) ? e.message : String(e)));
    return () => ac.abort();
  }, [provider]);

  const installHref = useMemo(
    () => (row ? `/keys?provider=${encodeURIComponent(row.provider)}#free` : "/keys#free"),
    [row],
  );

  function openSignup() {
    if (!row?.register_url) return;
    rememberRegisterIntent({
      providerId: row.provider,
      providerName: row.name,
      registerUrl: row.register_url,
    });
    window.open(row.register_url, "_blank", "noopener,noreferrer");
    setOpened(true);
  }

  return (
    <PageContainer>
      <PageHeader
        title="Register a free provider"
        description="Groq first. Deep-link into the provider signup, then install the key in OpenVault. Not the public /rates page."
      />

      {err ? <p className="mb-4 text-sm text-destructive">{err}</p> : null}

      {!provider && (
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {catalog.map((p) => (
            <li key={p.provider} className="rounded-2xl border border-border bg-card p-4">
              <p className="text-sm font-semibold text-foreground">{p.name}</p>
              <p className="mt-1 text-xs text-muted-foreground">{p.free_notes || p.tier}</p>
              <Button asChild className="mt-3" size="sm">
                <Link href={`/tool/register?provider=${encodeURIComponent(p.provider)}`}>
                  Register {p.name}
                </Link>
              </Button>
            </li>
          ))}
        </ul>
      )}

      {row && (
        <div className="max-w-xl space-y-4 rounded-2xl border border-border bg-card p-6">
          <h2 className="text-lg font-semibold text-foreground">{row.name}</h2>
          <p className="text-sm text-muted-foreground">
            {row.free_notes || "Create an account, copy the key, then install it."}
          </p>
          {row.spendable ? (
            <p className="text-xs text-muted-foreground">
              Wired into FreeRoute pooled spend ({row.chat_models[0] || "catalogued models"}).
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">
              Register-only -- this provider is not an OpenAI-compat /v1 hop yet.
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button onClick={openSignup}>Open {row.name} signup</Button>
            <Button asChild variant="outline">
              <Link href={installHref}>I have the key -- install</Link>
            </Button>
          </div>
          {opened && (
            <p className="text-sm text-muted-foreground">
              Signup opened in a new tab. Copy the key, then install it. OpenVault never
              scrapes the signup page.
            </p>
          )}
        </div>
      )}
    </PageContainer>
  );
}

export default function RegisterToolPage() {
  return (
    <Suspense
      fallback={
        <PageContainer>
          <p className="text-sm text-muted-foreground">Loading register tool...</p>
        </PageContainer>
      }
    >
      <RegisterInner />
    </Suspense>
  );
}
