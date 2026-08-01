"use client";

/**
 * FreeBuild plan / CI-CD surface — thin UI over existing /api/freebuild* +
 * /api/ship/aws-plan / domain-guide style routes. No proprietary FreeBuild stack.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  LONG_TIMEOUT_MS,
  apiGet,
  apiPost,
  isApiError,
} from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";

type PlanRow = {
  id?: string;
  ship_id?: string;
  target?: string;
  status?: string;
  [key: string]: unknown;
};

export default function ShipCicdPage() {
  const [path, setPath] = useState("");
  const [subdomain, setSubdomain] = useState("demo");
  const [plans, setPlans] = useState<PlanRow[]>([]);
  const [status, setStatus] = useState<Record<string, unknown> | null>(null);
  const [planOut, setPlanOut] = useState<Record<string, unknown> | null>(null);
  const [awsOut, setAwsOut] = useState<Record<string, unknown> | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [list, st] = await Promise.all([
        apiGet<{ ships?: PlanRow[]; plans?: PlanRow[]; items?: PlanRow[] }>("/api/freebuild"),
        apiGet<Record<string, unknown>>("/api/ship/freebuild/status"),
      ]);
      setPlans(list.ships || list.plans || list.items || []);
      setStatus(st);
    } catch (e) {
      setErr(isApiError(e) ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function makePlan() {
    if (!path.trim() || !subdomain.trim()) {
      setErr("Project path and subdomain are required");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const out = await apiPost<Record<string, unknown>>(
        "/api/freebuild/plan",
        {
          project_path: path.trim(),
          subdomain: subdomain.trim(),
          action: "install",
          simulate: true,
        },
        { timeoutMs: LONG_TIMEOUT_MS },
      );
      setPlanOut(out);
      await refresh();
    } catch (e) {
      setErr(isApiError(e) ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function awsPlan() {
    setBusy(true);
    setErr("");
    try {
      const out = await apiPost<Record<string, unknown>>(
        "/api/ship/aws-plan",
        {
          project_path: path.trim() || undefined,
          hostname: subdomain.trim() ? `${subdomain.trim()}.example.com` : undefined,
        },
        { timeoutMs: LONG_TIMEOUT_MS },
      );
      setAwsOut(out);
    } catch (e) {
      setErr(isApiError(e) ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function execute(id: string) {
    setBusy(true);
    setErr("");
    try {
      await apiPost(
        `/api/freebuild/${encodeURIComponent(id)}/execute`,
        { simulate: true },
        { timeoutMs: LONG_TIMEOUT_MS },
      );
      await refresh();
    } catch (e) {
      setErr(isApiError(e) ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageContainer>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <PageHeader
          title="FreeBuild / CI-CD"
          description="Plan, list, and execute FreeBuild jobs via OpenVault — local engine first."
        />
        <Button asChild variant="outline" size="sm">
          <Link href="/ship">Ship wizard</Link>
        </Button>
      </div>

      {status ? (
        <pre className="mb-4 max-h-32 overflow-auto rounded-xl border border-border bg-card p-3 font-mono text-[11px] text-muted-foreground">
          {JSON.stringify(status, null, 2)}
        </pre>
      ) : null}

      <div data-glass className="mb-5 space-y-3 rounded-2xl border border-border bg-card p-5">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="cicd-path">Project path</Label>
            <Input
              id="cicd-path"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="D:\path\to\app"
              disabled={busy}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="cicd-host">Subdomain</Label>
            <Input
              id="cicd-host"
              value={subdomain}
              onChange={(e) => setSubdomain(e.target.value)}
              placeholder="demo"
              disabled={busy}
            />
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button disabled={busy} onClick={() => void makePlan()}>
            Create FreeBuild plan
          </Button>
          <Button variant="outline" disabled={busy} onClick={() => void awsPlan()}>
            AWS plan (teach)
          </Button>
          <Button variant="ghost" disabled={busy} onClick={() => void refresh()}>
            Refresh list
          </Button>
        </div>
      </div>

      {err ? <p className="mb-4 text-sm text-destructive">{err}</p> : null}

      <section className="mb-5 space-y-2">
        <h2 className="text-sm font-semibold text-foreground">Plans</h2>
        {plans.length === 0 ? (
          <p className="text-sm text-muted-foreground">No FreeBuild plans yet.</p>
        ) : (
          <ul className="space-y-2">
            {plans.map((p) => {
              const id = String(p.id || p.ship_id || "");
              return (
                <li
                  key={id || JSON.stringify(p)}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border bg-card px-4 py-3 text-sm"
                >
                  <div className="min-w-0">
                    <p className="font-medium text-foreground">{id || "plan"}</p>
                    <p className="text-xs text-muted-foreground">
                      {String(p.target || "")} {String(p.status || "")}
                    </p>
                  </div>
                  {id ? (
                    <Button size="sm" disabled={busy} onClick={() => void execute(id)}>
                      Execute
                    </Button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {planOut ? (
        <pre className="mb-4 max-h-48 overflow-auto rounded-xl bg-background/80 p-3 font-mono text-[11px] text-muted-foreground">
          {JSON.stringify(planOut, null, 2)}
        </pre>
      ) : null}
      {awsOut ? (
        <pre className="max-h-48 overflow-auto rounded-xl bg-background/80 p-3 font-mono text-[11px] text-muted-foreground">
          {JSON.stringify(awsOut, null, 2)}
        </pre>
      ) : null}
    </PageContainer>
  );
}
