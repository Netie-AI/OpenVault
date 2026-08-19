"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch, isApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";
import { WarningCallout } from "@/components/ui/WarningCallout";

type CortexStatus = {
  online?: boolean;
  base_url?: string;
  detail?: string;
};

type OrchestrationSelection = {
  primary_model?: string;
  fallback_models?: string[];
  cortex_tier?: string;
  engine_id?: string;
  notes?: string;
};

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <p className="mb-1 text-xs text-muted-foreground">{label}</p>
      <p className="truncate text-sm font-medium text-foreground" title={value || undefined}>
        {value || "—"}
      </p>
    </div>
  );
}

export default function EnginePage() {
  const [status, setStatus] = useState<CortexStatus | null>(null);
  const [selection, setSelection] = useState<OrchestrationSelection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const [st, sel] = await Promise.all([
        apiFetch<CortexStatus>("/api/cortex/status"),
        apiFetch<OrchestrationSelection>("/api/orchestration/selection").catch(
          () => ({} as OrchestrationSelection),
        ),
      ]);
      setStatus(st);
      setSelection(sel);
    } catch (e) {
      setStatus(null);
      setSelection(null);
      setError(isApiError(e) ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const online = status?.online === true;
  const fallbacks = selection?.fallback_models ?? [];

  return (
    <PageContainer>
      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <PageHeader
          title="Engine"
          description="Cortex mesh selection — models filtered by vault keys in OpenMW."
        />
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void load()}>
          {busy ? "Refreshing…" : "Refresh"}
        </Button>
      </div>

      {error && (
        <WarningCallout tone="danger" title="Could not load engine state" description={error} className="mb-6" />
      )}

      <div className="space-y-6">
        <div data-glass className="rounded-2xl border border-border bg-card p-4">
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <h2 className="text-sm font-semibold text-foreground">Cortex</h2>
            <span
              className={
                status == null
                  ? "inline-flex items-center rounded-lg border border-border px-2.5 py-1 text-xs text-muted-foreground"
                  : online
                    ? "inline-flex items-center rounded-lg border border-success-border bg-success-bg px-2.5 py-1 text-xs font-medium text-success"
                    : "inline-flex items-center rounded-lg border border-destructive/40 bg-destructive/10 px-2.5 py-1 text-xs font-medium text-destructive"
              }
            >
              {status == null ? "Unknown" : online ? "Online" : "Offline"}
            </span>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Base URL" value={status?.base_url ?? ""} />
            <Field label="Detail" value={status?.detail ?? ""} />
          </div>
        </div>

        <div data-glass className="rounded-2xl border border-border bg-card p-4">
          <h2 className="mb-4 text-sm font-semibold text-foreground">Selection</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Primary model" value={selection?.primary_model ?? ""} />
            <Field label="Engine" value={selection?.engine_id ?? ""} />
            <Field label="Cortex tier" value={selection?.cortex_tier ?? ""} />
            <Field
              label="Fallback models"
              value={fallbacks.length > 0 ? fallbacks.join(", ") : ""}
            />
            {selection?.notes ? (
              <div className="sm:col-span-2">
                <Field label="Notes" value={selection.notes} />
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </PageContainer>
  );
}
