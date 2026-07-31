"use client";

import { useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { apiFetch, apiPost, isApiError, LONG_TIMEOUT_MS } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";
import { cn } from "@/lib/utils";

type Peer = {
  kind: string;
  name: string;
  base_url: string;
  status: string;
  detail: string;
  last_seen: number | null;
  approved: boolean;
};

type MeshState = {
  peers: Record<string, Peer>;
  auto_approve_loopback?: boolean;
  updated_at?: number;
};

type PerfectLocal = {
  ready: boolean;
  missing: string[];
  message: string;
};

type ConnectPack = {
  schema?: string;
  env?: Record<string, string>;
};

type MeshPayload = {
  mesh: MeshState;
  connect_pack: ConnectPack;
  perfect_local: PerfectLocal;
};

function peerTone(status: string): "success" | "danger" | "warning" | "neutral" {
  if (status === "online" || status === "approved") return "success";
  if (status === "offline" || status === "rejected") return "danger";
  if (status === "pending_approve") return "warning";
  return "neutral";
}

const BADGE: Record<"success" | "danger" | "warning" | "neutral", string> = {
  success: "border-success-border bg-success-bg text-success",
  danger: "border-danger-border bg-danger-bg text-danger",
  warning: "border-warning-border bg-warning-bg text-warning",
  neutral: "border-border bg-muted/40 text-muted-foreground",
};

function StatusBadge({
  label,
  tone,
}: {
  label: string;
  tone: "success" | "danger" | "warning" | "neutral";
}) {
  return (
    <span
      className={cn(
        "rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
        BADGE[tone],
      )}
    >
      {label}
    </span>
  );
}

function formatSeen(ts: number | null | undefined): string {
  if (ts == null) return "—";
  const d = new Date(ts * 1000);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

export default function PeersPage() {
  const [data, setData] = useState<MeshPayload | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  async function load(refresh = false) {
    setErr("");
    setLoading(true);
    try {
      if (refresh) {
        await apiPost("/api/local/mesh/refresh", undefined, { timeoutMs: LONG_TIMEOUT_MS });
      }
      // Mesh probes Cortex/OpenIDE; offline peers make this ~20s+ on a cold mesh.
      const payload = await apiFetch<MeshPayload>("/api/local/mesh", {
        timeoutMs: LONG_TIMEOUT_MS,
      });
      setData(payload);
    } catch (e) {
      setData(null);
      setErr(isApiError(e) ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const peers = useMemo(
    () => Object.entries(data?.mesh?.peers ?? {}).map(([key, peer]) => ({ key, ...peer })),
    [data],
  );

  const summary = useMemo(() => {
    let online = 0;
    let offline = 0;
    let approved = 0;
    for (const peer of peers) {
      if (peer.approved) approved += 1;
      if (peer.status === "online" || peer.status === "approved") online += 1;
      else if (peer.status === "offline") offline += 1;
    }
    return { online, offline, approved, total: peers.length };
  }, [peers]);

  const perfect = data?.perfect_local;

  return (
    <PageContainer>
      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <PageHeader
          title="Peers"
          description="Local mesh · OpenVault ↔ Cortex ↔ OpenIDE"
        />
        <Button
          variant="outline"
          size="sm"
          onClick={() => void load(true)}
          disabled={loading}
          className="shrink-0"
        >
          <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
          Refresh
        </Button>
      </div>

      {err ? <p className="mb-4 text-sm text-destructive">{err}</p> : null}

      {!err && !data && loading ? (
        <p className="text-sm text-muted-foreground">Loading mesh…</p>
      ) : null}

      {data ? (
        <>
          <div className="mb-4 flex flex-wrap gap-2 text-xs">
            <span className="rounded-full border border-border px-2.5 py-1 text-muted-foreground">
              Peers {summary.total}
            </span>
            <span className="rounded-full border border-success-border bg-success-bg px-2.5 py-1 text-success">
              Online {summary.online}
            </span>
            <span className="rounded-full border border-danger-border bg-danger-bg px-2.5 py-1 text-danger">
              Offline {summary.offline}
            </span>
            <span className="rounded-full border border-border px-2.5 py-1 text-muted-foreground">
              Approved {summary.approved}
            </span>
          </div>

          {perfect ? (
            <div
              data-glass
              className="mb-6 rounded-2xl border border-border bg-card p-4"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground">Perfect local</p>
                  <p className="mt-1 text-xs text-muted-foreground">{perfect.message}</p>
                  {perfect.missing?.length ? (
                    <p className="mt-2 text-xs text-warning">
                      Missing: {perfect.missing.join(", ")}
                    </p>
                  ) : null}
                </div>
                <StatusBadge
                  label={perfect.ready ? "ready" : "not ready"}
                  tone={perfect.ready ? "success" : "warning"}
                />
              </div>
            </div>
          ) : null}

          {peers.length === 0 ? (
            <div
              data-glass
              className="rounded-2xl border border-border bg-card p-6 text-center"
            >
              <p className="text-sm font-medium text-foreground">No mesh peers</p>
              <p className="mt-1 text-xs text-muted-foreground">
                The local mesh has not reported any peers yet.
              </p>
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {peers.map((peer) => (
                <div
                  key={peer.key}
                  data-glass
                  className="rounded-2xl border border-border bg-card p-5"
                >
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                      {peer.kind}
                    </span>
                    <div className="flex flex-wrap gap-1">
                      <StatusBadge label={peer.status} tone={peerTone(peer.status)} />
                      {peer.approved ? (
                        <StatusBadge label="approved" tone="success" />
                      ) : (
                        <StatusBadge label="unapproved" tone="neutral" />
                      )}
                    </div>
                  </div>
                  <h3 className="text-sm font-semibold text-foreground">{peer.name}</h3>
                  <p className="mt-1 break-all text-xs text-muted-foreground">
                    {peer.base_url}
                  </p>
                  {peer.detail ? (
                    <p className="mt-2 text-xs text-muted-foreground">{peer.detail}</p>
                  ) : null}
                  <p className="mt-2 text-xs text-muted-foreground">
                    Last seen {formatSeen(peer.last_seen)}
                  </p>
                </div>
              ))}
            </div>
          )}

          {data.connect_pack?.env ? (
            <div
              data-glass
              className="mt-6 rounded-2xl border border-border bg-card p-4"
            >
              <p className="text-sm font-medium text-foreground">Connect pack env</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {data.connect_pack.schema || "openvault.local.connect_pack"}
              </p>
              <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                {Object.entries(data.connect_pack.env).map(([key, value]) => (
                  <div key={key}>
                    <dt className="text-muted-foreground">{key}</dt>
                    <dd className="break-all font-medium text-foreground">{value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}
        </>
      ) : null}
    </PageContainer>
  );
}
