"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import BuildLogPane from "@/components/terminal/BuildLogPane";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";
import { Button } from "@/components/ui/button";
import { apiGet, isApiError } from "@/lib/api/client";
import { buildStreamUrl } from "@/lib/sse/client";

type Deployment = {
  deployment_id?: string;
  target?: string;
  ready?: boolean;
  public_url?: string;
  project_path?: string;
  mode?: string;
  steps?: Array<{ id?: string; title?: string; status?: string; detail?: string }>;
};

export default function ShipDeployPage() {
  const params = useParams();
  const id = String(params?.id || "");
  const [dep, setDep] = useState<Deployment | null>(null);
  const [err, setErr] = useState("");
  const streamUrl = useMemo(() => (id ? buildStreamUrl(id) : undefined), [id]);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    void (async () => {
      try {
        const data = await apiGet<Deployment>(`/api/ship/engine/${encodeURIComponent(id)}`);
        if (!cancelled) setDep(data);
      } catch (e) {
        if (!cancelled) setErr(isApiError(e) ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <PageContainer>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <PageHeader
          title="Deploy live"
          description={
            dep
              ? `${dep.target || "target"} · ${dep.mode || "engine"} · ${id.slice(0, 8)}…`
              : `Streaming deployment ${id.slice(0, 8)}…`
          }
        />
        <Button asChild variant="outline" size="sm">
          <Link href="/ship">Back to Ship</Link>
        </Button>
      </div>

      {err ? <p className="mb-4 text-sm text-destructive">{err}</p> : null}

      {dep?.public_url ? (
        <p className="mb-4 text-sm">
          Live:{" "}
          <a
            className="text-primary underline"
            href={dep.public_url}
            target="_blank"
            rel="noreferrer"
          >
            {dep.public_url}
          </a>
        </p>
      ) : null}

      <BuildLogPane streamUrl={streamUrl} autoStart height="480px" />
    </PageContainer>
  );
}
