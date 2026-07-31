"use client";

import { useEffect, useState } from "react";
import { apiFetch, isApiError } from "@/lib/api/client";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";

export default function EnginePage() {
  const [text, setText] = useState("Loading…");
  useEffect(() => {
    Promise.all([
      apiFetch<{ online?: boolean; base_url?: string; detail?: string }>("/api/cortex/status"),
      apiFetch<unknown>("/api/orchestration/selection").catch(() => ({})),
    ])
      .then(([st, sel]) => {
        setText(
          `${st.online ? "Online" : "Offline"} · ${st.base_url || ""} · ${st.detail || ""}\n` +
            `selection: ${JSON.stringify(sel, null, 2)}`,
        );
      })
      .catch((e) => setText(isApiError(e) ? e.message : String(e)));
  }, []);
  return (
    <PageContainer>
      <PageHeader
        title="Engine"
        description="Cortex mesh selection — models filtered by vault keys in OpenMW."
      />
      <pre
        data-glass
        className="overflow-auto whitespace-pre-wrap rounded-2xl border border-border bg-card p-4 text-xs text-muted-foreground"
      >
        {text}
      </pre>
    </PageContainer>
  );
}
