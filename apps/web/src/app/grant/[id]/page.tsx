"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { apiGet, apiPost, isApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";

// No user_code here on purpose. The API only returns the code to the app that
// asked for the grant; the human reads it off that app's screen and types it
// below. If this page could display it, any other local process could read it
// back off the same endpoint and approve its own grant (KB A-0009).
type GrantView = {
  grant_id?: string;
  client_name?: string;
  label?: string;
  status?: string;
};

export default function GrantDecidePage() {
  const params = useParams<{ id: string }>();
  const id = String(params?.id || "");
  const [row, setRow] = useState<GrantView | null>(null);
  const [code, setCode] = useState("");
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
      const data = await apiPost<GrantView>(`/api/local/grants/${id}/decide`, {
        approve,
        user_code: code.trim(),
      });
      setRow(data);
      setError("");
    } catch (err) {
      setError(isApiError(err) ? err.message : "Could not decide");
    } finally {
      setBusy(false);
    }
  }

  const pending = row?.status === "pending";
  const armed = pending && code.trim().length > 0;

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
            <p className="mt-3 text-xs text-muted-foreground">Status: {row.status}</p>
          </>
        )}
        {pending && (
          <>
            <p className="mt-4 text-sm text-muted-foreground">
              Type the code that app is showing. Only the app that asked knows it, so this is
              what says which process you are approving.
            </p>
            <Input
              className="mt-2 max-w-[10rem] font-mono text-lg uppercase tracking-widest"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              placeholder="A1B2"
              autoComplete="off"
              spellCheck={false}
              aria-label="Pairing code"
            />
            <div className="mt-5 flex flex-wrap gap-2">
              <Button onClick={() => void decide(true)} disabled={busy || !armed}>
                Grant
              </Button>
              <Button
                variant="outline"
                onClick={() => void decide(false)}
                disabled={busy || !armed}
              >
                Deny
              </Button>
            </div>
          </>
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
