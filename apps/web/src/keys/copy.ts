/**
 * Product-locked key UI copy.
 *
 * Subscribe names Cortex only. BYOK names the provider the user pasted.
 * Free is two short steps. Operator hop status is a different screen (R-0011).
 */

export const CORTEX_KEY_LABEL = "Cortex API key";

export const POWERED_BY = "Powered by top-tier AI";

export const SAFETY_DISCLOSURE =
  "Safety: Cortex uses this key. OpenVault keeps it in one vault. " +
  "Do not share it. Ship and leave-machine still go through the gate.";

export const SUBSCRIBE = {
  title: "Your Cortex API key",
  lead:
    "Get a Cortex API key. Use it with Cortex. OpenVault stores it -- one vault, one key.",
  button: "Get Cortex API key",
  issuedHeading: CORTEX_KEY_LABEL,
  issuedHint: "Copy this Cortex API key now. OpenVault will not show the full value again.",
  disclosure: `${SAFETY_DISCLOSURE} ${POWERED_BY}`,
  emptyHint: "No Cortex API key yet. Tap the button to issue one.",
} as const;

export const BYOK = {
  title: "Bring your own key",
  lead: "Paste a key you already have. We show the provider name you brought -- honest labels, not a guessed brand.",
  pasteLabel: "Paste your key",
  storeButton: "Store this key",
  unknownProvider: "Unknown provider — type the name you got this key from",
} as const;

export const FREE = {
  title: "Free keys",
  lead: "Two steps. Register, then install.",
  steps: [
    {
      n: 1,
      title: "Register",
      body: "Create a free account and copy the key it shows you.",
    },
    {
      n: 2,
      title: "Install",
      body: "Paste that key here. OpenVault encrypts it in the vault.",
    },
  ],
  cortexHint: "Want the easy path? Issue a Cortex API key on Subscribe -- no extra signup.",
} as const;

/** Terms that must never appear on the subscribe screen (product lock). */
export const FORBIDDEN_SUBSCRIBE_TERMS: readonly string[] = [
  "DeepSeek",
  "China",
  "Groq",
  "Gemini",
  "OpenRouter",
  "OpenAI",
];
