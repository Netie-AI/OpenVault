/**
 * FreeRoute Get-free-keys checklist (#60). Groq first. GitHub Models omitted.
 *
 * Client fallback when /api/freeroute/onboard is unreachable. Canonical table
 * lives in OpenMW/openmw/openvault/vault/free_keys_onboard.py.
 */

export const CF_WORKERS_AI_BASE_TEMPLATE =
  "https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1";

export interface FreeKeyOnboardRow {
  id: string;
  label: string;
  register_url: string;
  default_base_url: string;
  add_key_provider: string;
  role?: string;
  notes: string;
  required_first?: boolean;
  needs_account_id?: boolean;
  installed?: boolean;
  installed_key_id?: string | null;
  deep_link?: string;
  return_path?: string;
}

export const FREE_KEYS_ONBOARD: readonly FreeKeyOnboardRow[] = [
  {
    id: "groq",
    label: "Groq",
    register_url: "https://console.groq.com/keys",
    default_base_url: "https://api.groq.com/openai/v1",
    add_key_provider: "groq",
    notes: "Required first. Fast free-tier hop.",
    required_first: true,
  },
  {
    id: "google",
    label: "Google AI Studio",
    register_url: "https://aistudio.google.com/apikey",
    default_base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
    add_key_provider: "google",
    notes: "Prefer AQ. auth keys (AI Studio). AIza… still works.",
  },
  {
    id: "openrouter",
    label: "OpenRouter free",
    register_url: "https://openrouter.ai/keys",
    default_base_url: "https://openrouter.ai/api/v1",
    add_key_provider: "openrouter",
    notes: "Free models at $0 via :free suffix.",
  },
  {
    id: "cerebras",
    label: "Cerebras",
    register_url: "https://cloud.cerebras.ai",
    default_base_url: "https://api.cerebras.ai/v1",
    add_key_provider: "cerebras",
    notes: "Official trial may need a card.",
  },
  {
    id: "mistral",
    label: "Mistral",
    register_url: "https://console.mistral.ai/api-keys",
    default_base_url: "https://api.mistral.ai/v1",
    add_key_provider: "mistral",
    notes: "Phone/billing common on signup.",
  },
  {
    id: "huggingface",
    label: "Hugging Face",
    register_url: "https://huggingface.co/settings/tokens",
    default_base_url: "https://huggingface.co",
    add_key_provider: "huggingface",
    notes: "Founder-hold enroll optional.",
  },
  {
    id: "cloudflare",
    label: "Cloudflare Workers AI",
    register_url: "https://developers.cloudflare.com/workers-ai/get-started/rest-api/",
    default_base_url: CF_WORKERS_AI_BASE_TEMPLATE,
    add_key_provider: "custom",
    notes:
      "Account ID is not a secret. GET /models 405 is a probe mismatch, not a dead key.",
    needs_account_id: true,
  },
];

const ACCOUNT_ID_RE = /^[A-Fa-f0-9]{32}$/;

export function composeCloudflareWorkersAiBase(accountId: string): string {
  const aid = accountId.trim();
  if (!ACCOUNT_ID_RE.test(aid)) {
    throw new Error("Cloudflare Account ID must be the dashboard account id (not an API token)");
  }
  return CF_WORKERS_AI_BASE_TEMPLATE.replace("{ACCOUNT_ID}", aid);
}

export function isCloudflareWorkersAiBase(baseUrl: string): boolean {
  const url = baseUrl.trim().toLowerCase().replace(/\/+$/, "");
  return url.includes("api.cloudflare.com/client/v4/accounts/") && url.includes("/ai");
}

export function isProbeMismatchWarn(error: string | null | undefined): boolean {
  const text = (error || "").toLowerCase();
  return text.includes("405") && text.includes("probe mismatch");
}
