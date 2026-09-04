/**
 * Honest BYOK labels from a pasted secret.
 *
 * Strong prefixes only. Never invent a vendor. Never label an ov_ token as OpenAI.
 */

export interface ByokGuess {
  providerId: string | null;
  displayName: string;
  confidence: "strong" | "none";
}

const RULES: ReadonlyArray<{
  id: string;
  name: string;
  test: RegExp;
}> = [
  { id: "cortex", name: "Cortex API key", test: /^ov_[A-Za-z0-9_-]{16,}$/ },
  { id: "anthropic", name: "Anthropic", test: /^sk-ant-/i },
  { id: "openrouter", name: "OpenRouter", test: /^sk-or-v1-/i },
  { id: "groq", name: "Groq", test: /^gsk_/ },
  { id: "huggingface", name: "Hugging Face", test: /^hf_/ },
  { id: "google", name: "Google AI Studio", test: /^AIza[0-9A-Za-z_-]{10,}/ },
  { id: "cerebras", name: "Cerebras", test: /^csk-/i },
  { id: "fireworks", name: "Fireworks", test: /^fw_/ },
  { id: "together", name: "Together AI", test: /^tgp_v1_/ },
  { id: "github_models", name: "GitHub Models", test: /^(ghp_|gho_|ghu_|ghs_|github_pat_)/ },
  { id: "openai", name: "OpenAI", test: /^sk-(proj-)?[A-Za-z0-9_-]{16,}$/ },
];

export function guessByokProvider(secret: string): ByokGuess {
  const value = secret.trim();
  if (!value) {
    return { providerId: null, displayName: "", confidence: "none" };
  }
  for (const rule of RULES) {
    if (rule.test.test(value)) {
      return { providerId: rule.id, displayName: rule.name, confidence: "strong" };
    }
  }
  return { providerId: null, displayName: "", confidence: "none" };
}

/** Label shown after the user types or confirms a provider name. */
export function honestByokLabel(pastedProviderName: string, secret: string): string {
  const typed = pastedProviderName.trim();
  if (typed) return typed;
  const guess = guessByokProvider(secret);
  return guess.displayName;
}
