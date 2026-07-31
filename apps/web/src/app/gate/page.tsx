"use client";

import { useState } from "react";
import { apiPost, isApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";

/** Gate check is user-triggered — never auto-fires on mount. */
export default function GatePage() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  async function runCheck() {
    setBusy(true);
    try {
      const d = await apiPost<unknown>("/api/gate/check", {
        action: "deploy",
        bypass: false,
      });
      setText(JSON.stringify(d, null, 2));
    } catch (e) {
      setText(isApiError(e) ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageContainer>
      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <PageHeader
          title="Gate"
          description="OpenVault custody gate — bypass attempts WARN + deny. Runs only when you click."
        />
        <Button size="sm" disabled={busy} onClick={() => void runCheck()}>
          Run gate check
        </Button>
      </div>
      <pre
        data-glass
        className="overflow-auto whitespace-pre-wrap rounded-2xl border border-border bg-card p-4 text-xs text-muted-foreground"
      >
        {text || "Not run yet."}
      </pre>
    </PageContainer>
  );
}
