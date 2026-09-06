"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { apiGet, apiPost, isApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";

type GrantView = {
  grant_id?: string;
  client_name?: string;
  label?: string;
  user_code?: string;
  status?: string;
};

export default function GrantDecidePage() {
  const params = useParams<{ id: string }>();
  const id = String(params?.id || "");
  const [row, setRow] = useState<GrantView | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const data = await apiGet<GrantView>(`/api/local/grants/${id}`);
      setRow(data);
      setError("");
    } catch (err) {
      setRow(null);
      setError(isApiError(err) ? err.message : "Grant not found");
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function decide(approve: boolean) {
    setBusy(true);
    try {
      const data = await apiPost<GrantView>(`/api/local/grants/${id}/decide`, { approve });
      setRow(data);
      setError("");
    } catch (err) {
      setError(isApiError(err) ? err.message : "Could not decide");
    } finally {
      setBusy(false);
    }
  }

  const pending = row?.status === "pending";

  return (
    <PageContainer>
      <PageHeader
        title="Grant key"
        description="A local app asked for an OpenVault key. Approve here. The token goes to that app, not into this page."
      />
      <div className="max-w-lg rounded-2xl border border-border bg-card p-6">
        {error && <p className="mb-3 text-sm text-destructive">{error}</p>}
        {row && (
          <>
            <p className="text-sm text-muted-foreground">App</p>
            <p className="text-lg font-semibold text-foreground">{row.client_name}</p>
            <p className="mt-3 text-sm text-muted-foreground">Match this code</p>
            <p className="font-mono text-2xl tracking-widest text-foreground">{row.user_code}</p>
            <p className="mt-3 text-xs text-muted-foreground">Status: {row.status}</p>
          </>
        )}
        {pending && (
          <div className="mt-5 flex flex-wrap gap-2">
            <Button onClick={() => void decide(true)} disabled={busy}>
              Grant
            </Button>
            <Button variant="outline" onClick={() => void decide(false)} disabled={busy}>
              Deny
            </Button>
          </div>
        )}
        {row?.status === "ready" && (
          <p className="mt-4 text-sm text-muted-foreground">
            Granted. The other app can continue. This screen does not show the secret.
          </p>
        )}
        {row?.status === "denied" && (
          <p className="mt-4 text-sm text-muted-foreground">Denied. The other app gets no key.</p>
        )}
      </div>
    </PageContainer>
  );
}
