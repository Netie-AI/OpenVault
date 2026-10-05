"use client";

/**
 * Paste or pick a .env file. Preview is masked. Add posts /api/keys.
 * Provider cards stay on /providers; this does not rebuild them.
 */

import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { readAdminSession } from "@/lib/api/adminSession";
import { createKey } from "@/lib/api/keys";
import { previewEnvText, runEnvAdds, type EnvAddOutcome } from "@/lib/vault/envImport";

export function EnvTextImport({
  disabled = false,
  onImported,
}: {
  disabled?: boolean;
  onImported?: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [outcomes, setOutcomes] = useState<EnvAddOutcome[]>([]);
  const [notice, setNotice] = useState("");

  const preview = useMemo(() => previewEnvText(text), [text]);

  function onFile(file: File | undefined) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setText(typeof reader.result === "string" ? reader.result : "");
      setOutcomes([]);
      setNotice("");
    };
    reader.readAsText(file);
  }

  async function onAdd() {
    if (disabled || busy) return;
    setBusy(true);
    setNotice("");
    try {
      const rows = await runEnvAdds(text, (body) => createKey(body), [readAdminSession()]);
      setOutcomes(rows);
      const failed = rows.filter((row) => !row.ok).length;
      if (rows.length === 0) {
        setNotice("No known provider keys in that text.");
      } else if (failed === 0) {
        setText("");
        setNotice(`Added ${rows.length} key(s).`);
        onImported?.();
      } else {
        setNotice(`Added ${rows.length - failed} key(s). ${failed} failed.`);
        onImported?.();
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3" data-testid="env-text-import">
      <div className="space-y-1.5">
        <Label htmlFor="env-import-text">Paste a .env or several KEY=value lines</Label>
        <Textarea
          id="env-import-text"
          rows={6}
          spellCheck={false}
          placeholder={"GROQ_API_KEY=\nOPENROUTER_API_KEY=\nGOOGLE_API_KEY=\nNVIDIA_API_KEY=\nSEA_LION_API_KEY="}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setOutcomes([]);
            setNotice("");
          }}
        />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-sm text-muted-foreground">
          Choose .env file
          <input
            className="mt-1 block text-xs"
            type="file"
            accept=".env,.txt,text/plain"
            onChange={(e) => {
              onFile(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
        </label>
        <Button type="button" onClick={() => void onAdd()} disabled={disabled || busy || preview.length === 0}>
          {busy ? "Adding..." : "Add known keys"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        The list shows a mask, not the key. Unknown names are skipped.
      </p>
      {preview.length > 0 ? (
        <ul className="space-y-1 text-xs text-muted-foreground" data-testid="env-import-preview">
          {preview.map((row) => (
            <li key={row.envKey}>
              {row.envKey} - {row.provider} - {row.masked}
            </li>
          ))}
        </ul>
      ) : null}
      {outcomes.length > 0 ? (
        <ul className="space-y-1 text-xs text-muted-foreground" data-testid="env-import-outcomes">
          {outcomes.map((row) => (
            <li key={row.envKey}>
              {row.envKey} - {row.provider} - {row.ok ? "ok" : "fail"}
              {row.error ? ` - ${row.error}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
      {notice ? <p className="text-xs text-muted-foreground">{notice}</p> : null}
    </div>
  );
}
