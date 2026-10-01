"use client";

import { useEffect, useState, type FormEvent } from "react";
import { apiFetch, BROWSER_API_PREFIX, isApiError } from "@/lib/api/client";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";

type Card = {
  id: string;
  name: string;
  register_url: string;
  icon: string;
};

type Section = {
  id: string;
  title: string;
  note: string;
  cards: Card[];
};

type CardsPayload = {
  sections: Section[];
};

type AddBody = {
  label?: string;
  masked_id?: string;
  outcome?: string;
};

function scrub(text: string, secret: string): string {
  if (!secret || !text.includes(secret)) return text;
  return text.split(secret).join("");
}

function CardForm({ card, note }: { card: Card; note: string }) {
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  async function onSubmit(ev: FormEvent) {
    ev.preventDefault();
    const pasted = secret;
    setSecret("");
    setBusy(true);
    setMsg("");
    try {
      const res = await fetch("/api/provider-cards", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ provider: card.id, secret: pasted }),
      });
      const data = (await res.json().catch(() => null)) as AddBody | null;
      const label = scrub(String(data?.label ?? ""), pasted);
      const masked = scrub(String(data?.masked_id ?? ""), pasted);
      const outcome = scrub(String(data?.outcome ?? ""), pasted);
      setMsg([label, masked, outcome].filter(Boolean).join(" "));
    } catch {
      setMsg("test call failed (unreachable)");
    } finally {
      setBusy(false);
    }
  }

  return (
    <article
      data-provider={card.id}
      className="rounded-2xl border border-border bg-card p-4"
    >
      <div className="flex items-center gap-3">
        <img
          src={`${BROWSER_API_PREFIX}${card.icon}`}
          alt=""
          width={40}
          height={40}
        />
        <h3 className="text-sm font-semibold text-foreground">{card.name}</h3>
      </div>
      {note ? <p className="mt-2 text-xs text-muted-foreground">{note}</p> : null}
      <a
        className="mt-2 inline-block text-sm text-primary underline"
        href={card.register_url}
        target="_blank"
        rel="noopener noreferrer"
      >
        Get key
      </a>
      <form className="mt-3" onSubmit={(ev) => void onSubmit(ev)}>
        <label className="block text-xs text-muted-foreground">
          API key
          <input
            type="password"
            name="secret"
            autoComplete="off"
            spellCheck={false}
            value={secret}
            onChange={(ev) => setSecret(ev.target.value)}
            className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground"
          />
        </label>
        <button
          type="submit"
          disabled={busy}
          className="mt-3 rounded-full bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-60"
        >
          Test and add
        </button>
      </form>
      <p className="mt-2 min-h-[1.2em] text-xs text-muted-foreground">{msg}</p>
    </article>
  );
}

export default function ProvidersPage() {
  const [sections, setSections] = useState<Section[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    const ac = new AbortController();
    apiFetch<CardsPayload>("/api/providers/cards", { signal: ac.signal })
      .then((data) => setSections(data.sections ?? []))
      .catch((e: unknown) => {
        if (ac.signal.aborted) return;
        setErr(isApiError(e) ? e.message : "Could not load provider cards.");
      });
    return () => ac.abort();
  }, []);

  return (
    <PageContainer>
      <PageHeader
        title="Providers"
        description="Paste a key once. OpenVault tests it, then stores it."
      />
      {err ? <p className="mb-4 text-sm text-destructive">{err}</p> : null}
      {sections.map((section) => (
        <section key={section.id} data-section={section.id} className="mt-8">
          <h2 className="text-lg font-semibold text-foreground">{section.title}</h2>
          <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {section.cards.map((card) => (
              <CardForm key={card.id} card={card} note={section.note} />
            ))}
          </div>
        </section>
      ))}
    </PageContainer>
  );
}
