"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiGet, isApiError } from "@/lib/api/client";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";

type GrantView = {
  grant_id: string;
  client_name: string;
  user_code: string;
  status: string;
};

export default function GrantListPage() {
  const [rows, setRows] = useState<GrantView[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    const ac = new AbortController();
    apiGet<{ grants?: GrantView[] }>("/api/local/grants", { signal: ac.signal })
      .then((data) => setRows(data.grants ?? []))
      .catch((err) => setError(isApiError(err) ? err.message : "Could not list grants"));
    return () => ac.abort();
  }, []);

  return (
    <PageContainer>
      <PageHeader
        title="App grants"
        description="Local apps waiting for a key. Grant opens the matching screen. Passkeys are not this."
      />
      {error && <p className="text-sm text-destructive">{error}</p>}
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No pending grants. Another app can run{" "}
          <code className="text-foreground">openvault grant request --client MyApp</code>.
        </p>
      ) : (
        <ul className="max-w-lg space-y-2">
          {rows.map((row) => (
            <li key={row.grant_id}>
              <Link
                href={`/grant/${row.grant_id}`}
                className="flex items-center justify-between rounded-xl border border-border bg-card px-4 py-3 text-sm hover:bg-accent/40"
              >
                <span className="font-medium text-foreground">{row.client_name}</span>
                <span className="font-mono text-muted-foreground">{row.user_code}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </PageContainer>
  );
}
