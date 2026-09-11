"use client";

/**
 * FreeRoute control surface -- hops, spendable catalog, JWKS bind kids.
 * Cortex+safety story, not a fake OpenAI clone. Usage $/unit stays NEEDS-YOU.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";
import { Textarea } from "@/components/ui/textarea";
import { LONG_TIMEOUT_MS, apiGet, apiPost, isApiError } from "@/lib/api/client";

type Hop = {
  key_id?: string;
  label?: string;
  provider?: string;
  role?: string;
  circuit?: string;
  precheck_status?: string;
};

type Spendable = {
  id: string;
  name: string;
  tier: string;
  chat_models?: string[];
  register_url?: string;
};

type Status = {
  sealed?: boolean;
  kid_count?: number;
  kids?: string[];
  jwks_uri?: string;
  pooled_key_count?: number;
  hops?: Hop[];
  spendable?: Spendable[];
  usage_unit_status?: string;
};

type VaultStatus = { sealed?: boolean };

type ChatChoice = { message?: { content?: string } };
type ChatResponse = {
  choices?: ChatChoice[];
  error?: { message?: string };
};

export default function FreeRoutePage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [vault, setVault] = useState<VaultStatus | null>(null);
  const [err, setErr] = useState("");
  const [prompt, setPrompt] = useState("");
  const [reply, setReply] = useState("");
  const [chatBusy, setChatBusy] = useState(false);

  const load = useCallback(async () => {
    setErr("");
    try {
      const [st, vs] = await Promise.all([
        apiGet<Status>("/api/freeroute/status"),
        apiGet<VaultStatus>("/api/vault/status").catch(() => ({})),
      ]);
      setStatus(st);
      setVault(vs);
    } catch (e) {
      setErr(isApiError(e) ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const sendChat = useCallback(async () => {
    const text = prompt.trim();
    if (!text || chatBusy) return;
    setChatBusy(true);
    setReply("");
    setErr("");
    try {
      const out = await apiPost<ChatResponse>(
        "/v1/chat/completions",
        {
          model: "auto",
          messages: [{ role: "user", content: text }],
          stream: false,
        },
        { timeoutMs: LONG_TIMEOUT_MS },
      );
      const content = out.choices?.[0]?.message?.content?.trim();
      if (content) {
        setReply(content);
        return;
      }
      if (out.error?.message) {
        setReply(out.error.message);
        return;
      }
      setReply("FreeRoute returned no completion text.");
    } catch (e) {
      setReply(isApiError(e) ? e.message : String(e));
    } finally {
      setChatBusy(false);
    }
  }, [prompt, chatBusy]);

  const kids = status?.kids ?? [];
  const hops = status?.hops ?? [];
  const spendable = status?.spendable ?? [];

  return (
    <PageContainer>
      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <PageHeader
          title="FreeRoute"
          description="Pooled custody gateway. Cortex uses OpenVault keys; hop vendors stay off the Subscribe screen."
        />
        <Button variant="outline" size="sm" onClick={() => void load()}>
          Refresh
        </Button>
      </div>

      {err ? <p className="mb-4 text-sm text-destructive">{err}</p> : null}

      <div className="mb-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Vault" value={vault?.sealed || status?.sealed ? "sealed" : "unsealed"} />
        <Stat label="JWKS kids" value={String(status?.kid_count ?? kids.length)} />
        <Stat label="Pooled keys" value={String(status?.pooled_key_count ?? 0)} />
        <Stat label="Usage $/unit" value={status?.usage_unit_status ?? "NEEDS-YOU"} />
      </div>

      <section className="mb-6 rounded-2xl border border-border bg-card p-5">
        <h2 className="text-sm font-semibold text-foreground">Try a hop</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Sends to this machine&apos;s <code className="text-xs">POST /v1/chat/completions</code>.
          Loopback only. A refusal is shown as text, never as an empty success.
        </p>
        <Textarea
          className="mt-3 min-h-[96px]"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Ask through FreeRoute..."
          disabled={chatBusy}
        />
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" disabled={chatBusy || !prompt.trim()} onClick={() => void sendChat()}>
            {chatBusy ? "Routing..." : "Send"}
          </Button>
        </div>
        {reply ? (
          <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap rounded-xl border border-border bg-background p-3 text-sm text-foreground">
            {reply}
          </pre>
        ) : null}
      </section>

      <section className="mb-6 rounded-2xl border border-border bg-card p-5">
        <h2 className="text-sm font-semibold text-foreground">Cortex bind (JWKS)</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Public pin document at {status?.jwks_uri || "/.well-known/jwks.json"}. Mint stays
          loopback. Never paste an ov_ token into chat, PRs, or logs.
        </p>
        {kids.length ? (
          <ul className="mt-3 space-y-1 font-mono text-xs text-muted-foreground">
            {kids.map((kid) => (
              <li key={kid}>{kid}</li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-sm text-destructive">No kids yet -- Cortex cannot bind.</p>
        )}
        <div className="mt-4 flex flex-wrap gap-2">
          <Button asChild size="sm">
            <Link href="/keys#free">Get free keys</Link>
          </Button>
          <Button asChild size="sm" variant="outline">
            <Link href="/tool/register?provider=groq">Register Groq</Link>
          </Button>
          <Button asChild size="sm" variant="outline">
            <Link href="/system">Control plane</Link>
          </Button>
        </div>
      </section>

      <section className="mb-6">
        <h2 className="mb-3 text-sm font-semibold text-foreground">Spendable pooled hops</h2>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {spendable.map((p) => (
            <div key={p.id} className="rounded-2xl border border-border bg-card p-4">
              <p className="text-sm font-semibold text-foreground">{p.name}</p>
              <p className="mt-1 font-mono text-xs text-muted-foreground">{p.id}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {p.chat_models?.[0] || "catalogued"} · {p.tier}
              </p>
              <Button asChild className="mt-3" size="sm" variant="outline">
                <Link href={`/tool/register?provider=${encodeURIComponent(p.id)}`}>Register</Link>
              </Button>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold text-foreground">Fallback hops</h2>
        {hops.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No pooled hops yet. Register a free key, or the vault is sealed.
          </p>
        ) : (
          <ul className="space-y-2">
            {hops.map((h) => (
              <li
                key={String(h.key_id || h.label)}
                className="flex flex-wrap justify-between gap-2 rounded-xl border border-border bg-card px-4 py-3 text-sm"
              >
                <span className="font-medium text-foreground">
                  {h.label || h.provider} ({h.provider})
                </span>
                <span className="text-muted-foreground">
                  {h.role} · {h.precheck_status || "unknown"} · {h.circuit || "closed"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </PageContainer>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-border bg-card p-4">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold text-foreground">{value}</p>
    </div>
  );
}
