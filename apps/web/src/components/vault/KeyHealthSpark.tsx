"use client";

/** Tiny status sparkline from GET /api/keys/{id}/health samples. */

export type SparkSample = {
  t: number;
  status: string;
  latency_ms?: number | null;
};

function tone(status: string): string {
  if (status === "ok") return "bg-success";
  if (status === "rate_limit") return "bg-warning";
  if (status === "auth_fail" || status === "error" || status === "timeout") {
    return "bg-destructive";
  }
  return "bg-muted-foreground/40";
}

export function KeyHealthSpark({
  samples,
  uptimePct,
  p95Ms,
}: {
  samples: SparkSample[];
  uptimePct: number | null;
  p95Ms: number | null;
}) {
  const dots = samples.slice(-24);
  const caption =
    dots.length < 3
      ? "not enough data"
      : uptimePct != null
        ? `${uptimePct.toFixed(1)}% over 24h${p95Ms != null ? ` · p95 ${Math.round(p95Ms)}ms` : ""}`
        : "not enough data";

  return (
    <div className="mt-1.5 flex flex-col gap-0.5">
      <div className="flex h-3 items-end gap-px" aria-hidden>
        {dots.length === 0 ? (
          <span className="h-1 w-full rounded-sm bg-muted" />
        ) : (
          dots.map((s, i) => (
            <span
              key={`${s.t}-${i}`}
              className={`w-1.5 min-w-[3px] flex-1 rounded-sm ${tone(s.status)}`}
              style={{
                height: `${Math.max(
                  20,
                  Math.min(100, ((s.latency_ms ?? 50) / 500) * 100),
                )}%`,
              }}
              title={`${s.status}${s.latency_ms != null ? ` ${Math.round(s.latency_ms)}ms` : ""}`}
            />
          ))
        )}
      </div>
      <p className="text-[10px] text-muted-foreground">{caption}</p>
    </div>
  );
}
