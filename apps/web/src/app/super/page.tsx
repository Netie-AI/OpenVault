"use client";

import { useState } from "react";
import { apiFetch, isApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";

type Cloud = {
  id: string;
  name: string;
  ready: boolean;
  needs_local_build: boolean;
};

type Task = {
  id: string;
  engine: string;
  url: string;
  parallel_group: string;
  cloud?: string;
  light_app?: boolean;
  provider?: string | null;
  model?: string | null;
};

type Plan = {
  lanes: string[];
  parallel: boolean;
  light_app: boolean;
  recommended_cloud: string;
  model: { provider: string; model: string; key_present: boolean } | null;
  app: { framework?: string; category?: string; build_command?: string } | null;
  clouds: Cloud[];
  tasks: Task[];
  keys: string;
};

export default function SuperPage() {
  const [text, setText] = useState("ask groq and ship this small landing");
  const [projectPath, setProjectPath] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  async function planIt() {
    setErr("");
    setLoading(true);
    try {
      const next = await apiFetch<Plan>("/api/superapp/plan", {
        method: "POST",
        body: { text, project_path: projectPath },
      });
      setPlan(next);
    } catch (caught) {
      setPlan(null);
      setErr(isApiError(caught) ? caught.message : "Plan failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <PageContainer>
      <PageHeader
        title="Super"
        description="One plan. Auto-detect the model route and the cloud ship. Route and ship run together. Keys stay in the vault."
      />
      <form
        className="grid gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          void planIt();
        }}
      >
        <label className="grid gap-1 text-sm">
          What do you want
          <textarea
            className="min-h-24 rounded-xl border border-input bg-background p-3 text-sm"
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </label>
        <label className="grid gap-1 text-sm">
          Project folder (absolute path, optional)
          <input
            className="h-10 rounded-xl border border-input bg-background px-3 text-sm"
            value={projectPath}
            onChange={(event) => setProjectPath(event.target.value)}
            placeholder="C:\work\landing"
          />
        </label>
        <div>
          <Button type="submit" disabled={loading}>
            {loading ? "Planning" : "Plan route and ship"}
          </Button>
        </div>
      </form>
      {err ? <p className="mt-4 text-sm text-destructive">{err}</p> : null}
      {plan ? (
        <section className="mt-6 grid gap-3 text-sm">
          <p>
            Lanes: {plan.lanes.join(" + ")}
            {plan.parallel ? " (together)" : ""}
            {plan.light_app ? " -- light app" : ""}
          </p>
          <p>
            Model:{" "}
            {plan.model
              ? `${plan.model.provider} / ${plan.model.model} (${plan.model.key_present ? "key in vault" : "no key yet"})`
              : "none"}
          </p>
          <p>
            App: {plan.app?.framework || "none"}
            {plan.app?.build_command ? ` -- build: ${plan.app.build_command}` : ""}
          </p>
          <p>Recommended cloud: {plan.recommended_cloud || "none"}</p>
          <ul className="grid gap-1">
            {plan.clouds.map((cloud) => (
              <li key={cloud.id}>
                {cloud.name}: {cloud.ready ? "ready" : "needs a vault key"}
                {cloud.needs_local_build ? " -- build on this machine first" : ""}
              </li>
            ))}
          </ul>
          <ul className="grid gap-1">
            {plan.tasks.map((task) => (
              <li key={task.id}>
                {task.id} via {task.engine} -- {task.url}
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground">{plan.keys}</p>
        </section>
      ) : null}
    </PageContainer>
  );
}
