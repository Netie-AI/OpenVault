"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import {
  apiGet,
  apiPost,
  apiPut,
  isApiError,
} from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";
import { Tabs } from "@/components/ui/Tabs";
import { cn } from "@/lib/utils";

type TabKey = "strategy" | "hops" | "breakers" | "metrics";

type StrategyResponse = {
  strategy: string;
  strategies: string[];
  orderedExecutionKeys: string[];
};

type RouteTarget = {
  executionKey: string;
  provider: string;
  modelStr: string;
  connectionId?: string | null;
  weight?: number;
  priority?: number;
  cost?: number | null;
};

type TargetsResponse = {
  strategy: string;
  targets: RouteTarget[];
  ordered: Array<{
    executionKey: string;
    provider: string;
    modelStr: string;
  }>;
};

type TargetMetrics = {
  requests: number;
  successes: number;
  successRate: number;
  avgLatencyMs: number;
  uses: number;
};

type MetricsResponse = {
  metrics: Record<string, TargetMetrics>;
};

type BreakerStatus = {
  name: string;
  state: string;
  failureCount: number;
  lastFailureTime: number | null;
  retryAfterMs: number;
  openCycleCount: number;
  degradationThreshold: number;
  effectiveResetTimeoutMs: number;
  profile: string;
};

type BreakersResponse = {
  breakers: BreakerStatus[];
  count: number;
};

type Hop = {
  key_id: string;
  label: string;
  provider: string;
  role: string;
  priority: number;
  precheck_status: string;
  circuit: "closed" | "open" | "half_open" | string;
  failures: number;
  last_error: string | null;
  last_latency_ms: number | null;
};

type FallbackConfig = {
  role_order: string[];
  failure_threshold: number;
  open_seconds: number;
};

type FallbackStatus = {
  hops: Hop[];
  config: FallbackConfig;
};

const TAB_DEFS: { key: TabKey; label: string }[] = [
  { key: "strategy", label: "Strategy" },
  { key: "hops", label: "Targets / Hops" },
  { key: "breakers", label: "Breakers" },
  { key: "metrics", label: "Metrics" },
];

function formatStrategyLabel(name: string): string {
  return name
    .split("-")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function precheckTone(status: string): "success" | "danger" | "neutral" {
  if (status === "ok") return "success";
  if (status === "unknown") return "neutral";
  return "danger";
}

function circuitTone(state: string): "success" | "danger" | "warning" | "neutral" {
  const normalized = state.toLowerCase();
  if (normalized === "closed") return "success";
  if (normalized === "open") return "danger";
  if (normalized === "half_open" || normalized === "degraded") return "warning";
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

function EmptyCard({ title, hint }: { title: string; hint: string }) {
  return (
    <div
      data-glass
      className="rounded-2xl border border-border bg-card p-6 text-center"
    >
      <p className="text-sm font-medium text-foreground">{title}</p>
      <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}

/**
 * Route / LLM proxy surface. OmniRoute's dashboard is NOT embedded — we stole
 * their middleware into src/proxy.ts and port algorithms to FastAPI. This page
 * talks to our backend only.
 */
export default function RoutePage() {
  const [tab, setTab] = useState<TabKey>("strategy");
  const [strategyData, setStrategyData] = useState<StrategyResponse | null>(null);
  const [targetsData, setTargetsData] = useState<TargetsResponse | null>(null);
  const [metricsData, setMetricsData] = useState<MetricsResponse | null>(null);
  const [breakersData, setBreakersData] = useState<BreakersResponse | null>(null);
  const [fallbackStatus, setFallbackStatus] = useState<FallbackStatus | null>(null);

  const [selectedStrategy, setSelectedStrategy] = useState<string>("");
  const [strategySaving, setStrategySaving] = useState(false);
  const [strategyMsg, setStrategyMsg] = useState("");
  const [resettingKey, setResettingKey] = useState<string | null>(null);

  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [justWired, setJustWired] = useState<string | null>(null);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem("openvault.just_wired");
      if (!raw) return;
      const parsed = JSON.parse(raw) as { label?: string; at?: number };
      sessionStorage.removeItem("openvault.just_wired");
      if (parsed?.label && parsed.at && Date.now() - parsed.at < 60_000) {
        setJustWired(parsed.label);
      }
    } catch {
      /* ignore */
    }
  }, []);

  const load = useCallback(async () => {
    setErr("");
    setLoading(true);
    setStrategyMsg("");

    const results = await Promise.allSettled([
      apiGet<StrategyResponse>("/api/route/strategy"),
      apiGet<TargetsResponse>("/api/route/targets"),
      apiGet<MetricsResponse>("/api/route/metrics"),
      apiGet<BreakersResponse>("/api/route/breakers"),
      apiGet<FallbackStatus>("/api/fallback/status"),
    ]);

    const labels = ["strategy", "targets", "metrics", "breakers", "fallback status"];
    const errors: string[] = [];

    const [strategyRes, targetsRes, metricsRes, breakersRes, fallbackRes] = results;

    if (strategyRes.status === "fulfilled") {
      setStrategyData(strategyRes.value);
      setSelectedStrategy(strategyRes.value.strategy);
    } else {
      setStrategyData(null);
      errors.push(
        `${labels[0]}: ${isApiError(strategyRes.reason) ? strategyRes.reason.message : String(strategyRes.reason)}`,
      );
    }

    if (targetsRes.status === "fulfilled") {
      setTargetsData(targetsRes.value);
    } else {
      setTargetsData(null);
      errors.push(
        `${labels[1]}: ${isApiError(targetsRes.reason) ? targetsRes.reason.message : String(targetsRes.reason)}`,
      );
    }

    if (metricsRes.status === "fulfilled") {
      setMetricsData(metricsRes.value);
    } else {
      setMetricsData(null);
      errors.push(
        `${labels[2]}: ${isApiError(metricsRes.reason) ? metricsRes.reason.message : String(metricsRes.reason)}`,
      );
    }

    if (breakersRes.status === "fulfilled") {
      setBreakersData(breakersRes.value);
    } else {
      setBreakersData(null);
      errors.push(
        `${labels[3]}: ${isApiError(breakersRes.reason) ? breakersRes.reason.message : String(breakersRes.reason)}`,
      );
    }

    if (fallbackRes.status === "fulfilled") {
      setFallbackStatus(fallbackRes.value);
    } else {
      setFallbackStatus(null);
      errors.push(
        `${labels[4]}: ${isApiError(fallbackRes.reason) ? fallbackRes.reason.message : String(fallbackRes.reason)}`,
      );
    }

    setErr(errors.length === results.length ? errors.join(" · ") : errors.join(" · "));
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function saveStrategy() {
    if (!selectedStrategy) return;
    setStrategySaving(true);
    setStrategyMsg("");
    try {
      const data = await apiPut<StrategyResponse>("/api/route/strategy", {
        strategy: selectedStrategy,
      });
      setStrategyData((prev) =>
        prev
          ? {
              ...prev,
              strategy: data.strategy,
              orderedExecutionKeys: data.orderedExecutionKeys,
            }
          : {
              strategy: data.strategy,
              strategies: [],
              orderedExecutionKeys: data.orderedExecutionKeys,
            },
      );
      setSelectedStrategy(data.strategy);
      setStrategyMsg("Strategy saved.");
      void load();
    } catch (e) {
      setStrategyMsg(isApiError(e) ? e.message : String(e));
    } finally {
      setStrategySaving(false);
    }
  }

  async function resetBreaker(key: string) {
    setResettingKey(key);
    try {
      await apiPost<{ ok: boolean; breaker: BreakerStatus }>(
        `/api/route/breakers/${encodeURIComponent(key)}/reset`,
      );
      void load();
    } catch (e) {
      setErr(isApiError(e) ? e.message : String(e));
    } finally {
      setResettingKey(null);
    }
  }

  const strategies = strategyData?.strategies ?? [];
  const orderedKeys = strategyData?.orderedExecutionKeys ?? [];
  const targets = targetsData?.targets ?? [];
  const orderedTargets = targetsData?.ordered ?? [];
  const hops = fallbackStatus?.hops ?? [];
  const config = fallbackStatus?.config;
  const breakers = breakersData?.breakers ?? [];
  const metrics = metricsData?.metrics ?? {};

  const hopCounts = useMemo(() => {
    let ok = 0;
    let error = 0;
    let unknown = 0;
    for (const hop of hops) {
      if (hop.precheck_status === "ok") ok += 1;
      else if (hop.precheck_status === "unknown") unknown += 1;
      else error += 1;
    }
    return { ok, error, unknown, total: hops.length };
  }, [hops]);

  const metricsEntries = Object.entries(metrics);

  return (
    <PageContainer>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <PageHeader
          title="Route"
          description="LLM proxy · OmniRoute middleware in-process · FastAPI route & fallback APIs. No :20128 iframe."
        />
        <Button
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

      {justWired ? (
        <div
          data-glass
          className="mb-4 rounded-2xl border border-success-border bg-success-bg px-4 py-3 text-sm text-foreground"
        >
          Stored <span className="font-medium">{justWired}</span> — this page is the live
          OpenFree / fallback endpoint on{" "}
          <code className="text-xs">http://127.0.0.1:5000/v1</code>.
        </div>
      ) : null}

      <Tabs tabs={TAB_DEFS} value={tab} onChange={setTab} className="mb-6" />

      {err ? (
        <p className="mb-4 text-sm text-destructive">{err}</p>
      ) : null}

      {loading && !strategyData && !fallbackStatus ? (
        <p className="text-sm text-muted-foreground">Loading route data…</p>
      ) : null}

      {tab === "strategy" ? (
        <div className="space-y-4">
          {!strategyData && !loading ? (
            <EmptyCard
              title="Strategy API unavailable"
              hint="Could not load /api/route/strategy. Check that the OpenVault backend is running."
            />
          ) : strategyData ? (
            <>
              <div
                data-glass
                className="rounded-2xl border border-border bg-card p-5"
              >
                <p className="text-sm font-medium text-foreground">Current strategy</p>
                <p className="mt-1 text-lg font-semibold text-foreground">
                  {formatStrategyLabel(strategyData.strategy)}
                </p>
                <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                  {strategyData.strategy}
                </p>
              </div>

              <div
                data-glass
                className="rounded-2xl border border-border bg-card p-5"
              >
                <p className="mb-3 text-sm font-medium text-foreground">
                  Pick strategy
                </p>
                <div className="grid gap-2 sm:grid-cols-2">
                  {(strategies.length > 0
                    ? strategies
                    : [
                        "priority",
                        "weighted",
                        "fill-first",
                        "round-robin",
                        "p2c",
                        "random",
                        "least-used",
                        "cost-optimized",
                      ]).map((name) => {
                    const active = selectedStrategy === name;
                    return (
                      <button
                        key={name}
                        type="button"
                        onClick={() => setSelectedStrategy(name)}
                        className={cn(
                          "rounded-xl border px-4 py-3 text-left transition-colors",
                          active
                            ? "border-primary bg-primary/10"
                            : "border-border bg-muted/20 hover:bg-muted/40",
                        )}
                      >
                        <span className="text-sm font-medium text-foreground">
                          {formatStrategyLabel(name)}
                        </span>
                        <span className="mt-0.5 block font-mono text-[10px] text-muted-foreground">
                          {name}
                        </span>
                      </button>
                    );
                  })}
                </div>

                <div className="mt-4 flex flex-wrap items-center gap-3">
                  <Button
                    size="sm"
                    disabled={
                      strategySaving ||
                      !selectedStrategy ||
                      selectedStrategy === strategyData.strategy
                    }
                    onClick={() => void saveStrategy()}
                  >
                    {strategySaving ? "Saving…" : "Save strategy"}
                  </Button>
                  {strategyMsg ? (
                    <p
                      className={cn(
                        "text-xs",
                        strategyMsg === "Strategy saved."
                          ? "text-success"
                          : "text-destructive",
                      )}
                    >
                      {strategyMsg}
                    </p>
                  ) : null}
                </div>
              </div>

              {orderedKeys.length > 0 ? (
                <div
                  data-glass
                  className="rounded-2xl border border-border bg-card p-5"
                >
                  <p className="text-sm font-medium text-foreground">
                    Ordered execution keys
                  </p>
                  <ol className="mt-2 space-y-1 font-mono text-xs text-muted-foreground">
                    {orderedKeys.map((key, i) => (
                      <li key={key}>
                        {i + 1}. {key}
                      </li>
                    ))}
                  </ol>
                </div>
              ) : (
                <EmptyCard
                  title="No targets ordered"
                  hint="Register route targets via /api/route/targets or Vault keys to see ordering."
                />
              )}
            </>
          ) : null}
        </div>
      ) : null}

      {tab === "hops" ? (
        <div className="space-y-6">
          <section>
            <h2 className="mb-3 text-sm font-semibold text-foreground">
              Route targets
            </h2>
            {!targetsData && !loading ? (
              <EmptyCard
                title="Targets API unavailable"
                hint="Could not load /api/route/targets."
              />
            ) : targets.length === 0 ? (
              <EmptyCard
                title="No route targets"
                hint="PUT /api/route/targets or enable Vault keys to populate targets."
              />
            ) : (
              <div className="grid gap-3 lg:grid-cols-2">
                {targets.map((t) => (
                  <div
                    key={t.executionKey}
                    data-glass
                    className="rounded-2xl border border-border bg-card p-5"
                  >
                    <h3 className="truncate text-sm font-semibold text-foreground">
                      {t.executionKey}
                    </h3>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {t.provider} · {t.modelStr}
                    </p>
                    <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-3">
                      <div>
                        <dt className="text-muted-foreground">Priority</dt>
                        <dd className="font-medium text-foreground">
                          {t.priority ?? "—"}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Weight</dt>
                        <dd className="font-medium text-foreground">
                          {t.weight ?? "—"}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Cost</dt>
                        <dd className="font-medium text-foreground">
                          {t.cost != null ? t.cost : "—"}
                        </dd>
                      </div>
                    </dl>
                  </div>
                ))}
              </div>
            )}

            {orderedTargets.length > 0 ? (
              <div
                data-glass
                className="mt-4 rounded-2xl border border-border bg-card p-4"
              >
                <p className="text-sm font-medium text-foreground">
                  Strategy order preview
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {targetsData?.strategy
                    ? formatStrategyLabel(targetsData.strategy)
                    : "—"}
                </p>
                <ol className="mt-2 space-y-1 text-xs text-muted-foreground">
                  {orderedTargets.map((t, i) => (
                    <li key={t.executionKey}>
                      {i + 1}. {t.executionKey} ({t.provider}/{t.modelStr})
                    </li>
                  ))}
                </ol>
              </div>
            ) : null}
          </section>

          <section>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold text-foreground">
                Fallback hops
              </h2>
              {hops.length > 0 ? (
                <div className="flex flex-wrap gap-2 text-xs">
                  <span className="rounded-full border border-border px-2.5 py-1 text-muted-foreground">
                    Hops {hopCounts.total}
                  </span>
                  <span className="rounded-full border border-success-border bg-success-bg px-2.5 py-1 text-success">
                    OK {hopCounts.ok}
                  </span>
                  <span className="rounded-full border border-danger-border bg-danger-bg px-2.5 py-1 text-danger">
                    Error {hopCounts.error}
                  </span>
                  <span className="rounded-full border border-border px-2.5 py-1 text-muted-foreground">
                    Unknown {hopCounts.unknown}
                  </span>
                </div>
              ) : null}
            </div>

            {!fallbackStatus && !loading ? (
              <EmptyCard
                title="Fallback status unavailable"
                hint="Could not load /api/fallback/status."
              />
            ) : config ? (
              <div
                data-glass
                className="mb-4 rounded-2xl border border-border bg-card p-4"
              >
                <p className="text-sm font-medium text-foreground">Fallback config</p>
                <dl className="mt-2 grid gap-2 text-sm sm:grid-cols-3">
                  <div>
                    <dt className="text-xs text-muted-foreground">Role order</dt>
                    <dd className="font-medium text-foreground">
                      {config.role_order?.length
                        ? config.role_order.join(" → ")
                        : "—"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Failure threshold</dt>
                    <dd className="font-medium text-foreground">
                      {config.failure_threshold ?? "—"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Open seconds</dt>
                    <dd className="font-medium text-foreground">
                      {config.open_seconds != null ? `${config.open_seconds}s` : "—"}
                    </dd>
                  </div>
                </dl>
              </div>
            ) : null}

            {fallbackStatus && hops.length === 0 ? (
              <EmptyCard
                title="No proxy hops"
                hint="Add and enable keys in Vault to populate the fallback chain."
              />
            ) : (
              <div className="grid gap-3 lg:grid-cols-2">
                {hops.map((hop) => (
                  <div
                    key={hop.key_id}
                    data-glass
                    className="rounded-2xl border border-border bg-card p-5"
                  >
                    <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0">
                        <h3 className="truncate text-sm font-semibold text-foreground">
                          {hop.label || hop.key_id}
                        </h3>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          {hop.provider} · {hop.role} · priority {hop.priority}
                        </p>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        <StatusBadge
                          label={`precheck ${hop.precheck_status}`}
                          tone={precheckTone(hop.precheck_status)}
                        />
                        <StatusBadge
                          label={`circuit ${hop.circuit}`}
                          tone={circuitTone(hop.circuit)}
                        />
                      </div>
                    </div>

                    <dl className="grid gap-2 text-xs sm:grid-cols-2">
                      <div>
                        <dt className="text-muted-foreground">Failures</dt>
                        <dd className="font-medium text-foreground">{hop.failures}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Latency</dt>
                        <dd className="font-medium text-foreground">
                          {hop.last_latency_ms != null
                            ? `${Math.round(hop.last_latency_ms)} ms`
                            : "—"}
                        </dd>
                      </div>
                      {hop.last_error ? (
                        <div className="sm:col-span-2">
                          <dt className="text-muted-foreground">Last error</dt>
                          <dd className="mt-0.5 break-words font-medium text-destructive">
                            {hop.last_error}
                          </dd>
                        </div>
                      ) : null}
                    </dl>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      ) : null}

      {tab === "breakers" ? (
        <div className="space-y-4">
          {!breakersData && !loading ? (
            <EmptyCard
              title="Breakers API unavailable"
              hint="Could not load /api/route/breakers."
            />
          ) : breakers.length === 0 ? (
            <EmptyCard
              title="No circuit breakers"
              hint="Breakers register when routed providers are exercised. None are active yet."
            />
          ) : (
            <div className="grid gap-3 lg:grid-cols-2">
              {breakers.map((b) => (
                <div
                  key={b.name}
                  data-glass
                  className="rounded-2xl border border-border bg-card p-5"
                >
                  <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <h3 className="truncate text-sm font-semibold text-foreground">
                        {b.name}
                      </h3>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        profile {b.profile}
                      </p>
                    </div>
                    <StatusBadge
                      label={b.state}
                      tone={circuitTone(b.state)}
                    />
                  </div>

                  <dl className="grid gap-2 text-xs sm:grid-cols-2">
                    <div>
                      <dt className="text-muted-foreground">Failures</dt>
                      <dd className="font-medium text-foreground">
                        {b.failureCount}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Open cycles</dt>
                      <dd className="font-medium text-foreground">
                        {b.openCycleCount}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Retry after</dt>
                      <dd className="font-medium text-foreground">
                        {b.retryAfterMs} ms
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Reset timeout</dt>
                      <dd className="font-medium text-foreground">
                        {b.effectiveResetTimeoutMs} ms
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Degrade at</dt>
                      <dd className="font-medium text-foreground">
                        {b.degradationThreshold}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Last failure</dt>
                      <dd className="font-medium text-foreground">
                        {b.lastFailureTime != null
                          ? new Date(b.lastFailureTime * 1000).toLocaleString()
                          : "—"}
                      </dd>
                    </div>
                  </dl>

                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-4"
                    disabled={resettingKey === b.name}
                    onClick={() => void resetBreaker(b.name)}
                  >
                    {resettingKey === b.name ? "Resetting…" : "Reset breaker"}
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}

      {tab === "metrics" ? (
        <div className="space-y-4">
          {!metricsData && !loading ? (
            <EmptyCard
              title="Metrics API unavailable"
              hint="Could not load /api/route/metrics."
            />
          ) : metricsEntries.length === 0 ? (
            <EmptyCard
              title="No metrics yet"
              hint="Per-target request counters appear after routed traffic flows through the proxy."
            />
          ) : (
            <>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {metricsEntries.map(([key, m]) => (
                  <div
                    key={key}
                    data-glass
                    className="rounded-2xl border border-border bg-card p-5"
                  >
                    <h3 className="truncate text-sm font-semibold text-foreground">
                      {key}
                    </h3>
                    <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                      <div>
                        <dt className="text-muted-foreground">Requests</dt>
                        <dd className="font-medium tabular-nums text-foreground">
                          {m.requests}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Successes</dt>
                        <dd className="font-medium tabular-nums text-foreground">
                          {m.successes}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Success rate</dt>
                        <dd className="font-medium tabular-nums text-foreground">
                          {m.successRate}%
                        </dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Avg latency</dt>
                        <dd className="font-medium tabular-nums text-foreground">
                          {m.avgLatencyMs} ms
                        </dd>
                      </div>
                    </dl>
                  </div>
                ))}
              </div>

              <details className="rounded-2xl border border-border bg-card">
                <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-muted-foreground">
                  Raw JSON
                </summary>
                <pre
                  className="overflow-auto px-4 pb-4 text-xs text-muted-foreground"
                >
                  {JSON.stringify(metricsData, null, 2)}
                </pre>
              </details>
            </>
          )}
        </div>
      ) : null}
    </PageContainer>
  );
}
