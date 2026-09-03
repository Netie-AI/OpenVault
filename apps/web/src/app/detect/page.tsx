"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { apiFetch, isApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/PageContainer";
import { cn } from "@/lib/utils";

type Device = {
  kind: string;
  name: string;
  health_pct: number;
  status: string;
  metric_label: string;
  metric_value: string;
  secondary_label: string;
  secondary_value: string;
};

type ProfileSource = "live" | "fallback_flag" | "fallback_substituted" | string;

type Payload = {
  demo_mode?: boolean;
  profile_source?: ProfileSource;
  profile_degraded_reason?: string | null;
  source?: string;
  degraded_reason?: string | null;
  profile?: { gpu_name?: string; nvme_model?: string; nvme_seq_read_gbps?: number };
  devices?: Device[];
  observe_source?: string;
};

type SentinelDevices = {
  source?: string;
  degraded_reason?: string | null;
  ok?: boolean;
  devices?: Array<{
    device_path?: string;
    model?: string;
    serial?: string;
    size_bytes?: number;
    bus_type?: string;
    media_type?: string;
    [key: string]: unknown;
  }>;
};

function asHealthDevices(sentinel: SentinelDevices): Payload {
  const devices: Device[] = (sentinel.devices || []).map((d) => ({
    kind: String(d.bus_type || d.media_type || "nvme").toLowerCase(),
    name: String(d.model || d.device_path || "disk"),
    health_pct: 100,
    status: sentinel.degraded_reason ? "degraded" : "ok",
    metric_label: "Path",
    metric_value: String(d.device_path || "—"),
    secondary_label: "Serial",
    secondary_value: String(d.serial || "—"),
  }));
  const mock = sentinel.source === "mock";
  return {
    demo_mode: mock,
    profile_source: mock ? "fallback_flag" : "live",
    profile_degraded_reason: sentinel.degraded_reason,
    source: sentinel.source,
    degraded_reason: sentinel.degraded_reason,
    profile: {
      nvme_model: devices[0]?.name,
    },
    devices,
  };
}

function badgeFor(source: ProfileSource | undefined, demoMode: boolean | undefined) {
  const src = source || (demoMode ? "fallback_flag" : "live");
  if (src === "live") {
    return {
      label: "LIVE",
      className: "border-success-border bg-success-bg text-success",
      caption: null as string | null,
    };
  }
  if (src === "fallback_substituted") {
    return {
      label: "PLACEHOLDER HARDWARE",
      className: "border-warning-border bg-warning-bg text-warning",
      caption: null as string | null,
    };
  }
  return {
    label: "DEMO (--mock-health)",
    className: "border-border bg-muted text-muted-foreground",
    caption: null as string | null,
  };
}

export default function DetectPage() {
  const [data, setData] = useState<Payload | null>(null);
  const [source, setSource] = useState("—");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [via, setVia] = useState<"health" | "sentinel" | "">("");

  async function load() {
    setErr("");
    setLoading(true);
    try {
      let payload: Payload | null = null;
      let viaPath: "health" | "sentinel" = "health";
      try {
        payload = await apiFetch<Payload>("/api/health/devices");
      } catch (healthErr) {
        const sentinel = await apiFetch<SentinelDevices>("/api/sentinel/devices", {
          query: { mock: "true" },
        }).catch(() => {
          throw healthErr;
        });
        payload = asHealthDevices(sentinel);
        viaPath = "sentinel";
      }
      const obs = await apiFetch<{ source?: string }>("/api/observe/path").catch(
        (): { source?: string } => ({}),
      );
      setData({ ...payload, observe_source: obs.source ?? payload.observe_source });
      setSource(obs.source || payload.source || (payload.demo_mode ? "mock" : "live"));
      setVia(viaPath);
    } catch (e) {
      setErr(isApiError(e) ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const p = data?.profile || {};
  const badge = badgeFor(data?.profile_source, data?.demo_mode);
  const degraded =
    data?.profile_degraded_reason || data?.degraded_reason || null;

  return (
    <PageContainer>
      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-foreground">
            Detection
          </h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            Host inventory via OpenMW. LIVE only when profile_source is live — never
            when placeholder hardware was substituted.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void load()}
          disabled={loading}
          className="shrink-0"
        >
          <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
          Refresh
        </Button>
      </div>

      <div
        data-glass
        className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-card p-4"
      >
        <div className="min-w-0">
          <p className="text-sm font-medium text-foreground">
            {p.gpu_name || "CPU host"} · {p.nvme_model || "storage"}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {p.nvme_seq_read_gbps != null
              ? `${Number(p.nvme_seq_read_gbps).toFixed(1)} GB/s NVMe`
              : "—"}
            {" · "}
            profile {data?.profile_source || "—"} · path-trace {source.toUpperCase()}
            {via ? ` · via ${via}` : ""}
          </p>
          {degraded ? (
            <p className="mt-1 text-xs text-warning" title={degraded}>
              {degraded}
            </p>
          ) : null}
        </div>
        <span
          className={cn(
            "rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide",
            badge.className,
          )}
          title={degraded || undefined}
        >
          {badge.label}
        </span>
      </div>

      {err ? <p className="mb-4 text-sm text-destructive">{err}</p> : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {(data?.devices || []).map((d) => (
          <div
            key={d.name + d.kind}
            data-glass
            className="rounded-2xl border border-border bg-card p-5"
          >
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                {d.kind}
              </span>
              <span className="rounded-full border border-border px-2 py-0.5 text-[10px] text-muted-foreground">
                {d.status}
              </span>
            </div>
            <p className="text-sm font-medium text-foreground">{d.name}</p>
            <p className="mt-2 text-xs text-muted-foreground">
              {d.metric_label}: {d.metric_value}
            </p>
            <p className="text-xs text-muted-foreground">
              {d.secondary_label}: {d.secondary_value}
            </p>
          </div>
        ))}
      </div>
    </PageContainer>
  );
}
