"use client";

import { useState } from "react";
import { apiPost, isApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";
import { WarningCallout } from "@/components/ui/WarningCallout";

/** Actions the gate API accepts — mirrors OpenMW GateAction. */
const GATE_ACTIONS = ["deploy", "leave", "run", "retrieve", "connect"] as const;
type GateAction = (typeof GATE_ACTIONS)[number];

type GateCheckResult = {
  allowed?: boolean;
  action?: string;
  reasons?: string[];
  keys_ready?: boolean;
  locate?: {
    sealed?: boolean;
    key_count?: number;
    providers?: string[];
    project_path?: string;
    destination?: string;
    fallback_hops?: number;
  };
  required_providers?: string[];
  firewall?: { allowed?: boolean; reasons?: string[] };
};

function pill(ok: boolean | undefined, yes: string, no: string, idle = "—") {
  if (ok === undefined) {
    return (
      <span className="inline-flex items-center rounded-lg border border-border px-2.5 py-1 text-xs text-muted-foreground">
        {idle}
      </span>
    );
  }
  return (
    <span
      className={
        ok
          ? "inline-flex items-center rounded-lg border border-success-border bg-success-bg px-2.5 py-1 text-xs font-medium text-success"
          : "inline-flex items-center rounded-lg border border-destructive/40 bg-destructive/10 px-2.5 py-1 text-xs font-medium text-destructive"
      }
    >
      {ok ? yes : no}
    </span>
  );
}

/** Gate check is user-triggered — never auto-fires on mount. */
export default function GatePage() {
  const [action, setAction] = useState<GateAction>("deploy");
  const [result, setResult] = useState<GateCheckResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function runCheck() {
    setBusy(true);
    setError(null);
    try {
      const d = await apiPost<GateCheckResult>("/api/gate/check", {
        action,
        bypass: false,
      });
      setResult(d);
    } catch (e) {
      setResult(null);
      setError(isApiError(e) ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const sealed = result?.locate?.sealed === true;
  const reasons = result?.reasons ?? [];

  return (
    <PageContainer>
      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <PageHeader
          title="Gate"
          description="OpenVault custody gate — bypass attempts WARN + deny. Runs only when you click."
        />
        <div className="flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor="gate-action">
            Gate action
          </label>
          <select
            id="gate-action"
            value={action}
            onChange={(e) => setAction(e.target.value as GateAction)}
            className="h-8 rounded-lg border border-input bg-background px-3 text-xs text-foreground"
          >
            {GATE_ACTIONS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
          <Button size="sm" disabled={busy} onClick={() => void runCheck()}>
            {busy ? "Checking…" : "Run gate check"}
          </Button>
        </div>
      </div>

      {error && (
        <WarningCallout tone="danger" title="Gate check failed" description={error} className="mb-6" />
      )}

      {!result && !error && (
        <p className="text-sm text-muted-foreground">Not run yet. Pick an action and run a check.</p>
      )}

      {result && (
        <div className="space-y-6">
          <div
            data-glass
            className="grid gap-3 rounded-2xl border border-border bg-card p-4 sm:grid-cols-2 lg:grid-cols-4"
          >
            <div>
              <p className="mb-1.5 text-xs text-muted-foreground">Decision</p>
              {pill(result.allowed, "Allowed", "Denied")}
            </div>
            <div>
              <p className="mb-1.5 text-xs text-muted-foreground">Keys ready</p>
              {pill(result.keys_ready, "Ready", "Not ready")}
            </div>
            <div>
              <p className="mb-1.5 text-xs text-muted-foreground">Vault sealed</p>
              {sealed
                ? pill(false, "Unsealed", "Sealed")
                : pill(true, "Unsealed", "Sealed")}
            </div>
            <div>
              <p className="mb-1.5 text-xs text-muted-foreground">Action</p>
              <span className="inline-flex items-center rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-foreground">
                {result.action ?? action}
              </span>
            </div>
          </div>

          <div data-glass className="rounded-2xl border border-border bg-card p-4">
            <h2 className="mb-2 text-sm font-semibold text-foreground">Reasons</h2>
            {reasons.length === 0 ? (
              <p className="text-sm text-muted-foreground">No reasons returned.</p>
            ) : (
              <ul className="list-inside list-disc space-y-1 text-sm text-foreground">
                {reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            )}
          </div>

          {(result.locate || (result.required_providers && result.required_providers.length > 0)) && (
            <div data-glass className="rounded-2xl border border-border bg-card p-4">
              <h2 className="mb-3 text-sm font-semibold text-foreground">Locate / providers</h2>
              <dl className="grid gap-2 text-sm sm:grid-cols-2">
                {typeof result.locate?.key_count === "number" && (
                  <>
                    <dt className="text-muted-foreground">Enabled keys</dt>
                    <dd className="text-foreground">{result.locate.key_count}</dd>
                  </>
                )}
                {result.locate?.providers && result.locate.providers.length > 0 && (
                  <>
                    <dt className="text-muted-foreground">Providers present</dt>
                    <dd className="text-foreground">{result.locate.providers.join(", ")}</dd>
                  </>
                )}
                {result.required_providers && result.required_providers.length > 0 && (
                  <>
                    <dt className="text-muted-foreground">Required providers</dt>
                    <dd className="text-foreground">{result.required_providers.join(", ")}</dd>
                  </>
                )}
                {typeof result.locate?.fallback_hops === "number" && (
                  <>
                    <dt className="text-muted-foreground">Fallback hops</dt>
                    <dd className="text-foreground">{result.locate.fallback_hops}</dd>
                  </>
                )}
              </dl>
            </div>
          )}
        </div>
      )}
    </PageContainer>
  );
}
