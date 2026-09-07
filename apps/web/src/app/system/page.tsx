"use client";

/**
 * SYSTEM control plane -- entitlements / unlock / seats / bind.
 * Loopback-only API. Not the Marketing netie.ai /rates page. Usage $/unit
 * stays NEEDS-YOU (DR-0013).
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";
import { apiGet, isApiError } from "@/lib/api/client";

type Bind = {
  public_bind_allowed?: boolean;
  default_host?: string;
  internal_writers_url?: string;
  jwks_uri?: string;
  jwks_url?: string;
  mint_loopback_only?: boolean;
  live_key_id?: string | null;
  live_key_secret_manager?: string;
};

type Plan = {
  id: string;
  display_name: string;
  display_usd_prefix?: string;
  family?: string;
};

type Catalog = {
  public_rate_page?: boolean;
  usage?: { unit_status?: string; priced?: boolean };
  plans?: Record<string, Plan>;
  bind?: Bind;
};

export default function SystemPage() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [bind, setBind] = useState<Bind | null>(null);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    setErr("");
    try {
      const [cat, b] = await Promise.all([
        apiGet<Catalog>("/api/system/catalog"),
        apiGet<Bind>("/api/system/bind"),
      ]);
      setCatalog(cat);
      setBind(b);
    } catch (e) {
      setErr(
        isApiError(e)
          ? e.status === 403
            ? "Control plane is loopback-only. Open this console on the vault machine."
            : e.message
          : String(e),
      );
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const plans = Object.values(catalog?.plans ?? {});

  return (
    <PageContainer>
      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <PageHeader
          title="Control plane"
          description="Locked display SKUs. Usage $/unit stays NEEDS-YOU. Not the public /rates page."
        />
        <Button variant="outline" size="sm" onClick={() => void load()}>
          Refresh
        </Button>
      </div>

      {err ? <p className="mb-4 text-sm text-destructive">{err}</p> : null}

      <section className="mb-6 rounded-2xl border border-border bg-card p-5">
        <h2 className="text-sm font-semibold text-foreground">Bind</h2>
        <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
          <Row k="Public :5000" v={String(bind?.public_bind_allowed ?? false)} />
          <Row k="Default host" v={bind?.default_host ?? "127.0.0.1"} />
          <Row k="Writers" v={bind?.internal_writers_url ?? "--"} />
          <Row k="JWKS" v={bind?.jwks_uri ?? "/.well-known/jwks.json"} />
          <Row k="Mint" v={bind?.mint_loopback_only ? "loopback only" : "check policy"} />
          <Row k="LIVE_KEY_ID" v={bind?.live_key_id ?? "(set LIVE_KEY_ID env -- id only)"} />
          <Row k="Secret Manager" v={bind?.live_key_secret_manager ?? "openvault-dms-writer-token"} />
        </dl>
        <p className="mt-3 text-xs text-muted-foreground">
          Never paste ov_ tokens. Cortex prove refreshes JWKS; dms#116 is verify-only remount
          after this bind exists.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button asChild size="sm">
            <Link href="/freeroute">FreeRoute</Link>
          </Button>
          <Button asChild size="sm" variant="outline">
            <Link href="/keys">Keys</Link>
          </Button>
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold text-foreground">Display SKUs (not a bill)</h2>
        <p className="mb-3 text-sm text-muted-foreground">
          Usage unit: {catalog?.usage?.unit_status ?? "NEEDS-YOU"} · priced={" "}
          {String(catalog?.usage?.priced ?? false)} · public rate page={" "}
          {String(catalog?.public_rate_page ?? false)}
        </p>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {plans.map((p) => (
            <div key={p.id} className="rounded-2xl border border-border bg-card p-4">
              <p className="text-sm font-semibold text-foreground">{p.display_name}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {p.display_usd_prefix} · {p.family} · display only
              </p>
            </div>
          ))}
        </div>
      </section>
    </PageContainer>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted-foreground">{k}</dt>
      <dd className="font-mono text-xs text-foreground">{v}</dd>
    </div>
  );
}
