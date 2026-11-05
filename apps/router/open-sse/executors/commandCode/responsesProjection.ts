// Responses-shape detection for the Command Code executor. The /alpha/generate
// CLI fallback and its Responses projection were removed: that endpoint takes the
// Command Code CLI's own wire shape and version headers, which FreeRoute does not
// send.

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * True for an OpenAI Responses-shaped body (`input`, not `messages`). Command Code
 * serves OpenAI models on BOTH /chat/completions and /responses, but only the
 * Responses endpoint honors `reasoning: {"effort": "none"}` (the chat validator
 * accepts low|medium|high|xhigh|max only, and silently ignores a nested
 * `reasoning.effort`). Route Responses-shaped bodies there so a no-thinking
 * request can actually disable reasoning.
 *
 * `messages` is the Chat discriminator and WINS when both are present: `input` is
 * only consulted when `messages` is absent. A body carrying both is malformed,
 * and defaulting to the flat Chat surface keeps routing predictable if a payload
 * override ever injects `messages` into a Responses request.
 */
export function isResponsesShapedBody(body: unknown): boolean {
  if (!isRecord(body)) return false;
  return body.input !== undefined && body.messages === undefined;
}
