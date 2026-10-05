/**
 * Client-side .env import for known provider names.
 *
 * Parsing and masking happen in the browser. The preview never carries the
 * secret. One-click add posts each row to /api/keys. sea_lion is a catalog
 * id, but POST /api/keys only accepts ProviderKind, which has no sea_lion,
 * so that row is stored as custom with the public SEA-LION base URL.
 */

import { redactShown } from "../api/adminSession";
import { composeCloudflareWorkersAiBase } from "./freeKeysOnboard";

export type EnvKeyRole = "primary" | "backup" | "cheap" | "free";

export interface EnvPreviewRow {
  envKey: string;
  provider: string;
  masked: string;
}

export interface EnvAddBody {
  label: string;
  provider: string;
  secret: string;
  role: EnvKeyRole;
  base_url?: string;
  custody: "pooled";
}

export interface EnvAddOutcome {
  envKey: string;
  provider: string;
  ok: boolean;
  error: string;
}

interface KnownEnv {
  provider: string;
  postProvider: string;
  role: EnvKeyRole;
  baseUrl?: string;
}

const SEA_LION_BASE = "https://api.sea-lion.ai/v1";

const KNOWN: Readonly<Record<string, KnownEnv>> = {
  NETIE_ENGINE_KEY: { provider: "cortex", postProvider: "cortex", role: "backup" },
  CURSOR_API_KEY: { provider: "custom", postProvider: "custom", role: "backup" },
  OMNIROUTE_API_KEY: { provider: "custom", postProvider: "custom", role: "backup" },
  GROQ_API_KEY: { provider: "groq", postProvider: "groq", role: "free" },
  OPENROUTER_API_KEY: { provider: "openrouter", postProvider: "openrouter", role: "free" },
  GOOGLE_API_KEY: { provider: "google", postProvider: "google", role: "free" },
  GEMINI_API_KEY: { provider: "google", postProvider: "google", role: "free" },
  GOOGLE_AISTUDIO_FREE: { provider: "google", postProvider: "google", role: "free" },
  HF_TOKEN: { provider: "huggingface", postProvider: "huggingface", role: "free" },
  HUGGINGFACE_API_KEY: { provider: "huggingface", postProvider: "huggingface", role: "free" },
  HUGGING_FACE_HUB_TOKEN: { provider: "huggingface", postProvider: "huggingface", role: "free" },
  CLOUDFLARE_API_TOKEN: { provider: "custom", postProvider: "custom", role: "free" },
  CF_API_TOKEN: { provider: "custom", postProvider: "custom", role: "free" },
  DEEPSEEK_API_KEY: { provider: "deepseek", postProvider: "deepseek", role: "cheap" },
  SEA_LION_API_KEY: {
    provider: "sea_lion",
    postProvider: "custom",
    role: "free",
    baseUrl: SEA_LION_BASE,
  },
  CEREBRAS_API_KEY: { provider: "cerebras", postProvider: "cerebras", role: "free" },
  MISTRAL_API_KEY: { provider: "mistral", postProvider: "mistral", role: "free" },
  NVIDIA_API_KEY: { provider: "nvidia", postProvider: "nvidia", role: "free" },
  NVIDIA_NIM_API_KEY: { provider: "nvidia", postProvider: "nvidia", role: "free" },
  FREENVIDIA_API_KEY: { provider: "nvidia", postProvider: "nvidia", role: "free" },
  TOGETHER_API_KEY: { provider: "together", postProvider: "together", role: "backup" },
  FIREWORKS_API_KEY: { provider: "fireworks", postProvider: "fireworks", role: "backup" },
  SILICONFLOW_API_KEY: { provider: "siliconflow", postProvider: "siliconflow", role: "backup" },
  DEEPGRAM_API_KEY: { provider: "deepgram", postProvider: "deepgram", role: "backup" },
  ANTHROPIC_API_KEY: { provider: "anthropic", postProvider: "anthropic", role: "backup" },
  OPENAI_API_KEY: { provider: "openai", postProvider: "openai", role: "backup" },
  OLLAMA_API_KEY: { provider: "ollama", postProvider: "ollama", role: "free" },
  VLLM_API_KEY: { provider: "custom", postProvider: "custom", role: "backup" },
  GH_TOKEN: { provider: "custom", postProvider: "custom", role: "backup" },
  GITHUB_TOKEN: { provider: "custom", postProvider: "custom", role: "backup" },
  OPENSHIP_API_TOKEN: { provider: "custom", postProvider: "custom", role: "backup" },
  LITELLM_API_KEY: { provider: "litellm", postProvider: "litellm", role: "backup" },
};

const CF_TOKEN_KEYS = new Set(["CLOUDFLARE_API_TOKEN", "CF_API_TOKEN"]);
const CF_ACCOUNT_ID = "CLOUDFLARE_ACCOUNT_ID";

const PLACEHOLDERS = new Set([
  "changeme",
  "change-me",
  "placeholder",
  "todo",
  "none",
  "null",
  "unset",
  "your-key",
  "your-key-here",
  "your_api_key",
]);

interface ParsedLine {
  key: string;
  value: string;
}

interface EnvPlan {
  envKey: string;
  provider: string;
  body: EnvAddBody;
}

export function maskEnvSecret(secret: string): string {
  if (!secret) return "";
  if (secret.length <= 8) return "*".repeat(secret.length);
  const hidden = Math.min(8, secret.length - 4);
  return `${secret.slice(0, 4)}...${"*".repeat(hidden)}`;
}

function isPlaceholder(value: string): boolean {
  const candidate = value.trim();
  if (candidate.length < 8) return true;
  const lowered = candidate.toLowerCase();
  if (PLACEHOLDERS.has(lowered)) return true;
  if (
    lowered.startsWith("your") ||
    lowered.startsWith("<") ||
    lowered.startsWith("${") ||
    lowered.startsWith("changeme") ||
    lowered.startsWith("example")
  ) {
    return true;
  }
  if (candidate.includes("\u2026") || candidate.startsWith("\u2022\u2022")) return true;
  return false;
}

/** Parse KEY=value lines. Values stay with the caller; this does not mask. */
export function parseEnvLines(text: string): ParsedLine[] {
  const rows: ParsedLine[] = [];
  for (const raw of text.split(/\r?\n/)) {
    let line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    if (line.toLowerCase().startsWith("export ")) line = line.slice(7).trim();
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim().replace(/^\uFEFF/, "");
    let value = line.slice(eq + 1).trim();
    if (
      value.length >= 2 &&
      (value[0] === '"' || value[0] === "'") &&
      value[0] === value[value.length - 1]
    ) {
      value = value.slice(1, -1);
    }
    if (!key) continue;
    rows.push({ key, value });
  }
  return rows;
}

function plansFromText(text: string): EnvPlan[] {
  const parsed = parseEnvLines(text);
  const values = new Map<string, string>();
  for (const row of parsed) values.set(row.key.trim().toUpperCase(), row.value);

  let cfBase = "";
  const account = (values.get(CF_ACCOUNT_ID) ?? "").trim();
  if (account) {
    try {
      cfBase = composeCloudflareWorkersAiBase(account);
    } catch {
      cfBase = "";
    }
  }

  const plans: EnvPlan[] = [];
  const seen = new Set<string>();
  for (const row of parsed) {
    const envKey = row.key.trim().toUpperCase();
    if (seen.has(envKey)) continue;
    const known = KNOWN[envKey];
    if (!known) continue;
    const secret = (values.get(envKey) ?? "").trim();
    if (isPlaceholder(secret)) continue;
    seen.add(envKey);
    let base = known.baseUrl ?? "";
    if (CF_TOKEN_KEYS.has(envKey) && cfBase) base = cfBase;
    const body: EnvAddBody = {
      label: envKey.slice(0, 80),
      provider: known.postProvider,
      secret,
      role: known.role,
      custody: "pooled",
    };
    if (base) body.base_url = base;
    plans.push({ envKey, provider: known.provider, body });
  }
  return plans;
}

/** Masked rows only. The return value must not contain a full secret. */
export function previewEnvText(text: string): EnvPreviewRow[] {
  return plansFromText(text).map((plan) => ({
    envKey: plan.envKey,
    provider: plan.provider,
    masked: maskEnvSecret(plan.body.secret),
  }));
}

/**
 * Post each known row. Outcomes name the env key and ok/fail only.
 * Secret bodies are scrubbed out of error text.
 */
export async function runEnvAdds(
  text: string,
  post: (body: EnvAddBody) => Promise<unknown>,
  extraHidden: readonly string[] = [],
): Promise<EnvAddOutcome[]> {
  const plans = plansFromText(text);
  const outcomes: EnvAddOutcome[] = [];
  for (const plan of plans) {
    try {
      await post(plan.body);
      outcomes.push({
        envKey: plan.envKey,
        provider: plan.provider,
        ok: true,
        error: "",
      });
    } catch (err) {
      const raw = err instanceof Error ? err.message : "add failed";
      outcomes.push({
        envKey: plan.envKey,
        provider: plan.provider,
        ok: false,
        error: redactShown(raw, [plan.body.secret, ...extraHidden]),
      });
    }
  }
  return outcomes;
}
