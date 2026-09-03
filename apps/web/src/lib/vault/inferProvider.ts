/**
 * Work out which provider a pasted key belongs to, from the key itself.
 *
 * Why this exists: asking someone to pick a provider, a role, a base URL and a
 * label before they can store a key is four decisions for information the key
 * already carries. Nearly every provider prefixes its keys distinctively, so we
 * can fill all four in and let the user correct the rare miss afterwards.
 *
 * Ordering is load-bearing. `sk-ant-`, `sk-or-v1-` and `sk-proj-` are all
 * valid `sk-` keys, so the specific patterns must be tested before the generic
 * one. The list is ordered most-specific-first and matched in order — do not
 * sort it.
 */

export interface ProviderGuess {
  /** Catalog provider id, or null when nothing matched. */
  providerId: string | null;
  /** How much we trust the guess. `weak` means "prefilled, please check". */
  confidence: "strong" | "weak" | "none";
  /** Human-facing reason, shown next to the prefilled field. */
  reason: string;
}

interface Rule {
  id: string;
  /** Matched against the trimmed secret. */
  test: RegExp;
  label: string;
  confidence?: "strong" | "weak";
}

const RULES: readonly Rule[] = [
  // ── Distinctive multi-part prefixes: unambiguous, check first ──────────
  { id: "anthropic", test: /^sk-ant-/i, label: "Anthropic key prefix sk-ant-" },
  { id: "openrouter", test: /^sk-or-v1-/i, label: "OpenRouter key prefix sk-or-v1-" },
  // Catalog id is `github_models`, not `github` — a mismatch here would be
  // recognised and then rejected as "not in the catalog".
  {
    id: "github_models",
    test: /^(ghp_|gho_|ghu_|ghs_|github_pat_)/,
    label: "GitHub token prefix",
  },

  // ── Single-token prefixes ─────────────────────────────────────────────
  { id: "groq", test: /^gsk_/, label: "Groq key prefix gsk_" },
  { id: "huggingface", test: /^hf_/, label: "Hugging Face token prefix hf_" },
  { id: "google", test: /^AIza[0-9A-Za-z_-]{10,}/, label: "Google API key prefix AIza" },
  // Google AI Studio issues a second, newer shape alongside `AIza…`. Without
  // this the console reports "Unrecognised key format" for a key it fully
  // supports, and the user has to know to pick the provider by hand.
  { id: "google", test: /^AQ\.[0-9A-Za-z_-]{16,}$/, label: "Google AI Studio key prefix AQ." },
  { id: "xai", test: /^xai-/i, label: "xAI key prefix xai-" },
  { id: "perplexity", test: /^pplx-/i, label: "Perplexity key prefix pplx-" },
  { id: "replicate", test: /^r8_/, label: "Replicate token prefix r8_" },
  { id: "cerebras", test: /^csk-/i, label: "Cerebras key prefix csk-" },
  { id: "nvidia", test: /^nvapi-/i, label: "NVIDIA NIM key prefix nvapi-" },
  { id: "fireworks", test: /^fw_/, label: "Fireworks key prefix fw_" },
  { id: "together", test: /^tgp_v1_/, label: "Together key prefix tgp_v1_" },
  { id: "mistral", test: /^ms-/i, label: "Mistral key prefix ms-" },
  { id: "deepseek", test: /^sk-[0-9a-f]{32}$/i, label: "DeepSeek key shape", confidence: "weak" },

  // ── Generic OpenAI-style. Must come last: it also matches the above ───
  { id: "openai", test: /^sk-(proj-)?[A-Za-z0-9_-]{16,}$/, label: "OpenAI key prefix sk-" },
];

/**
 * Guess the provider for a secret.
 *
 * `known` is the set of provider ids the catalog actually offers. A rule that
 * fires for a provider we cannot vault is worse than no guess at all — it would
 * prefill a provider the backend then rejects — so those are downgraded to
 * "no match" and the user picks manually.
 */
export function inferProvider(secret: string, known?: ReadonlySet<string>): ProviderGuess {
  const value = secret.trim();
  if (!value) {
    return { providerId: null, confidence: "none", reason: "" };
  }

  for (const rule of RULES) {
    if (!rule.test.test(value)) continue;
    if (known && !known.has(rule.id)) {
      // Recognised the vendor but we have no catalog entry to attach it to.
      return {
        providerId: null,
        confidence: "none",
        reason: `Looks like a ${rule.id} key, but that provider is not in the catalog yet`,
      };
    }
    return {
      providerId: rule.id,
      confidence: rule.confidence ?? "strong",
      reason: rule.label,
    };
  }

  return {
    providerId: null,
    confidence: "none",
    reason: "Unrecognised key format — pick the provider yourself",
  };
}

/**
 * True when clipboard / paste text is shaped like a provider API key.
 *
 * Used by ClipDrop to decide whether to interrupt the user. Ordinary passwords
 * and short strings must return false — false positives would pop the vault
 * every time someone copies a bank PIN.
 */
export function looksLikeApiSecret(text: string): boolean {
  const value = text.trim();
  if (value.length < 16 || value.length > 512) return false;
  if (/\s/.test(value)) return false;
  return RULES.some((rule) => rule.test.test(value));
}

/**
 * A label the user did not have to think of.
 *
 * Suffixing the key's last four characters keeps two keys for the same
 * provider distinguishable in a list, which is the only job a label has here.
 */
export function suggestLabel(providerName: string, secret: string): string {
  const tail = secret.trim().slice(-4);
  if (!tail || tail.length < 4) return providerName;
  return `${providerName} ····${tail}`;
}
