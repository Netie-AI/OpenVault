"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiGet, apiPost, isApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type HelpStep = { n?: string; do?: string };
type LadderRow = {
  rank?: number;
  id?: string;
  name?: string;
  kind?: string;
  register_url?: string;
  free_notes?: string;
};

type FreeHelp = {
  next_steps?: HelpStep[];
  ladder?: LadderRow[];
  help?: string;
  priced?: boolean;
};

type HopChip = {
  provider?: string;
  precheck_status?: string;
  circuit?: string;
  remaining_cooldown_ms?: number;
};

export function FreeRouteSmoke({ onSent }: { onSent?: () => void }) {
  const [prompt, setPrompt] = useState("Say hi in one word.");
  const [out, setOut] = useState("");
  const [busy, setBusy] = useState(false);
  const [help, setHelp] = useState<FreeHelp | null>(null);
  const [hops, setHops] = useState<HopChip[]>([]);

  useEffect(() => {
    void apiGet<FreeHelp>("/api/providers/free")
      .then((data) => setHelp(data))
      .catch(() => setHelp(null));
    void apiGet<{ hops?: HopChip[] }>("/api/fallback/status")
      .then((data) =>
        setHops(
          (data.hops || []).map((h) => ({
            provider: h.provider,
            precheck_status: h.precheck_status,
            circuit: h.circuit,
            remaining_cooldown_ms: h.remaining_cooldown_ms,
          })),
        ),
      )
      .catch(() => setHops([]));
  }, []);

  async function smokeChat() {
    setBusy(true);
    setOut("");
    try {
      const data = await apiPost<{
        choices?: Array<{ message?: { content?: string } }>;
      }>("/v1/chat/completions", {
        model: "auto",
        messages: [{ role: "user", content: prompt || "hi" }],
      });
      const text = data.choices?.[0]?.message?.content;
      setOut(text || JSON.stringify(data).slice(0, 400));
    } catch (e) {
      const msg = isApiError(e) ? e.message : String(e);
      const plain500 = /HTTP 500/i.test(msg);
      setOut(
        plain500
          ? msg +
              " Live OpenVault returned text/plain 500. json_safe is in the tree; restart :5000 to load it. Do not invent dollars."
          : msg,
      );
    } finally {
      setBusy(false);
      onSent?.();
    }
  }

  const steps = help?.next_steps ?? [];
  const ladder = help?.ladder ?? [];

  return (
    <div data-glass className="rounded-2xl border border-border bg-card p-4 text-sm">
      <p className="font-medium text-foreground">FreeRoute smoke (HT2 is human)</p>
      <p className="mt-1 text-xs text-muted-foreground">
        POST /v1/chat/completions with model=auto. A 503/429 is a real refusal.
        Meters record tokens; priced stays false. Do not invent dollars.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <Input
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          className="min-w-[12rem] flex-1"
          aria-label="FreeRoute smoke prompt"
        />
        <Button size="sm" disabled={busy} onClick={() => void smokeChat()}>
          {busy ? "Sending..." : "Send"}
        </Button>
      </div>
      {out ? (
        <pre className="mt-3 max-h-32 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">
          {out}
        </pre>
      ) : null}
      {hops.length > 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">
          Fallback hops (no key ids):{" "}
          {hops
            .map((h) => {
              const cool =
                typeof h.remaining_cooldown_ms === "number" && h.remaining_cooldown_ms > 0
                  ? ` cool${h.remaining_cooldown_ms}ms`
                  : "";
              return `${h.provider || "?"}:${h.precheck_status || "?"}/${h.circuit || "?"}${cool}`;
            })
            .join(", ")}
        </p>
      ) : null}
      {steps.length > 0 ? (
        <ol className="mt-3 list-decimal space-y-1 pl-4 text-xs text-muted-foreground">
          {steps.map((step) => (
            <li key={step.n || step.do}>{step.do}</li>
          ))}
        </ol>
      ) : null}
      {ladder.length > 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">
          Free ladder (local first):{" "}
          {ladder
            .slice(0, 6)
            .map((row) => row.id)
            .filter(Boolean)
            .join(", ")}
        </p>
      ) : null}
      <p className="mt-3 text-xs">
        <Link href="/vault" className="underline underline-offset-2">
          Vault: register then POST /api/keys
        </Link>
        {" · "}
        <Link href="/providers" className="underline underline-offset-2">
          Catalog
        </Link>
      </p>
    </div>
  );
}
