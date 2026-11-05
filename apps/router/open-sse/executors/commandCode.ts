import { MUSE_SPARK_PATTERN } from "./base/reasoningEffort.ts";
import { REGISTRY } from "../config/providerRegistry.ts";
import { isResponsesShapedBody } from "./commandCode/responsesProjection.ts";
import {
  BaseExecutor,
  mergeUpstreamExtraHeaders,
  sanitizeReasoningEffortForProvider,
  type ExecuteInput,
} from "./base.ts";
import { applyReasoningEffortRecovery } from "./base/reasoningEffortRecovery.ts";

type JsonRecord = Record<string, unknown>;

// Defensive server-side ceiling for a CLIENT-SUPPLIED max_tokens:
// any request with params.max_tokens > 200_000 is rejected with a 400
// "Too big: expected number to be <=200000 at params.max_tokens". We only clamp
// a client-supplied value down; we never fabricate this number for requests
// that omit the field.
//
// The quoted error names `params.max_tokens`, which is the /alpha/generate shape.
// The flat Chat surface uses top-level `max_tokens` and the Responses surface
// uses `max_output_tokens`; Command Code documents the 200_000 limit only for
// the first. The same gateway fronts all three, so this constant is applied to
// every output-cap field on the assumption the ceiling is endpoint-wide. If a
// live request ever 400s on max_output_tokens at a different bound, split the
// constant per surface rather than loosening this one.
const MAX_COMMAND_CODE_TOKENS = 200_000;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function numberValue(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function clampMaxTokens(value: unknown): number | undefined {
  const numeric = numberValue(value);
  if (numeric === undefined || numeric <= 0) return undefined;
  return Math.min(Math.floor(numeric), MAX_COMMAND_CODE_TOKENS);
}

/**
 * muse-spark (Meta) models are served through command-code with a hidden
 * server-side reasoning phase that consumes the entire output budget before any
 * visible content is emitted. With small caller-set budgets the upstream
 * answers HTTP 200 with an empty message and `completion_tokens == max_tokens`
 * (the symptom this fix targets: `out=64, reasoning=61` with null content);
 * chatCore then flags the fake success as "Provider returned empty content".
 *
 * Floor raised budgets only — explicit large budgets are untouched, and no
 * budget is synthesized when the caller set none. Detection is prefix-aware so
 * provider-prefixed ids (`meta/muse-spark-1.2-contributor`, `cmd/meta/muse-…`)
 * are caught, not just bare `muse-spark-*`.
 */
const MUSE_SPARK_MIN_OUTPUT_TOKENS = 512;

function applyMuseSparkMinOutputTokens(
  model: string,
  body: JsonRecord,
  field: "max_tokens" | "max_output_tokens" = "max_tokens"
): void {
  if (!MUSE_SPARK_PATTERN.test(model)) return;
  const current = body[field];
  if (typeof current !== "number" || !Number.isFinite(current)) return;
  if (current >= MUSE_SPARK_MIN_OUTPUT_TOKENS) return;
  body[field] = MUSE_SPARK_MIN_OUTPUT_TOKENS;
}

/**
 * Command Code serves most models under a vendor-prefixed wire id (e.g.
 * `xiaomi/mimo-v2.5`, `deepseek/deepseek-v4-pro`, `moonshotai/Kimi-K2.6`).
 * The command-code registry ids already carry the vendor prefix, so a bare id
 * reaching the executor is an operator-set custom model (e.g. the Vision Bridge
 * picker, #10809). Map the small set of documented bare ids to their
 * vendor-prefixed wire form; anything with an explicit `/` (or already wired)
 * passes through untouched. Kept minimal and doc-backed.
 */
const COMMAND_CODE_BARE_MODEL_VENDOR_PREFIX: Readonly<Record<string, string>> = {
  "mimo-v2.5": "xiaomi/mimo-v2.5",
  "mimo-v2.5-pro": "xiaomi/mimo-v2.5-pro",
};

function normalizeCommandCodeWireModel(model: string): string {
  const trimmed = String(model || "").trim();
  if (!trimmed) return trimmed;
  const bare = trimmed.replace(/^(?:command-code|cmd)\//, "");
  if (bare.includes("/")) return bare;
  return COMMAND_CODE_BARE_MODEL_VENDOR_PREFIX[bare] ?? bare;
}

// Responses-shape detection and Responses -> Chat projection live in
// ./commandCode/responsesProjection.ts (kept out of this file for the 1200-line
// file-size gate).

// ── OpenAi Flat Body Builder (/provider/v1/chat/completions) ─────────────────

function buildOpenAiBody(model: string, body: unknown, stream: boolean): { body: JsonRecord } {
  const input = isRecord(body) ? { ...(body as JsonRecord) } : {};

  const resolvedModel = normalizeCommandCodeWireModel(
    typeof input.model === "string" && input.model.trim().length > 0 ? input.model : model
  );

  const out: JsonRecord = {
    ...input,
    model: resolvedModel,
    stream: stream === true,
  };

  // Forward max_tokens only when the client actually supplied a positive value
  // (clamped to the endpoint ceiling). Omitting it lets the provider's upstream
  // apply the model's own native default; a non-positive value such as -1
  // ("let the server choose") must be omitted, NOT coerced to 1 (#5166).
  if (isResponsesShapedBody(out)) {
    // Responses shape: the cap lives on max_output_tokens; leave it clamped and
    // never fabricate a Chat-shaped max_tokens alongside it.
    const maxOutput = clampMaxTokens(out.max_output_tokens);
    delete out.max_tokens;
    delete out.max_completion_tokens;
    delete out.max_output_tokens;
    if (maxOutput !== undefined) {
      out.max_output_tokens = maxOutput;
    }
    applyMuseSparkMinOutputTokens(resolvedModel, out, "max_output_tokens");
    return { body: out };
  }

  const maxTokens = clampMaxTokens(input.max_tokens ?? input.max_completion_tokens);
  delete out.max_tokens;
  delete out.max_completion_tokens;
  if (maxTokens !== undefined) {
    out.max_tokens = maxTokens;
  }
  applyMuseSparkMinOutputTokens(resolvedModel, out);

  return { body: out };
}

// ── CommandCodeExecutor ──────────────────────────────────────────────────────

export class CommandCodeExecutor extends BaseExecutor {
  constructor(provider = "command-code") {
    super(provider, REGISTRY["command-code"]);
  }

  buildUrl() {
    const baseUrl = (this.config.baseUrl || "https://api.commandcode.ai").replace(/\/$/, "");
    return `${baseUrl}${this.config.chatPath || "/provider/v1/chat/completions"}`;
  }

  /**
   * OpenAI Responses endpoint for models whose targetFormat is
   * `openai-responses`. Same base + auth as chat; Command Code serves OpenAI and
   * open models on both surfaces, but only /responses honors
   * `reasoning: {"effort": "none"}` — the chat validator's effort enum has no
   * disable value and silently drops a nested `reasoning.effort` (verified live
   * 2026-09-24: /responses + effort none → reasoning_tokens 0; /chat +
   * `reasoning:{effort:"none"}` → reasoning_tokens 41).
   */
  buildResponsesUrl() {
    const baseUrl = (this.config.baseUrl || "https://api.commandcode.ai").replace(/\/$/, "");
    return `${baseUrl}/provider/v1/responses`;
  }

  async execute({ model, body, stream, credentials, signal, upstreamExtraHeaders }: ExecuteInput) {
    const apiKey = credentials?.apiKey || credentials?.accessToken;
    if (!apiKey) throw new Error("Command Code API key required");

    const abortSignal = signal || undefined;
    const sanitizedBody = sanitizeReasoningEffortForProvider(body, this.provider, model);
    const { body: initialTransformedBody } = buildOpenAiBody(model, sanitizedBody, stream);
    let transformedBody: unknown = initialTransformedBody;
    // Route by body shape: a Responses-shaped body (targetFormat openai-responses)
    // must hit /provider/v1/responses, where `reasoning: {"effort":"none"}` is
    // honored; the chat endpoint silently drops it.
    const url = isResponsesShapedBody(transformedBody) ? this.buildResponsesUrl() : this.buildUrl();

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
      Accept: stream ? "text/event-stream" : "application/json",
    };
    mergeUpstreamExtraHeaders(headers, upstreamExtraHeaders);

    let upstream = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(transformedBody),
      signal: abortSignal,
    });

    // #14629: reach the reactive reasoning_effort 400 clamp-and-retry chain,
    // otherwise unreachable here since this override never calls
    // super.execute() (see open-sse/executors/base/reasoningEffortRecovery.ts).
    const recovery = await applyReasoningEffortRecovery({
      response: upstream,
      url,
      provider: this.provider,
      model,
      body: transformedBody,
      fetchOptions: { method: "POST", headers, signal: abortSignal },
      fetchFn: (fetchUrl, fetchOpts) => fetch(fetchUrl, fetchOpts),
    });
    upstream = recovery.response;
    transformedBody = recovery.body;

    if (upstream.ok) {
      return { response: upstream, url, headers, transformedBody };
    }

    const errorText = await upstream.text().catch(() => {
      console.warn("[commandCode] upstream text failed");
      return "";
    });
    return {
      response: new Response(errorText || `Command Code API error ${upstream.status}`, {
        status: upstream.status,
        statusText: upstream.statusText,
        headers: upstream.headers,
      }),
      url,
      headers,
      transformedBody,
    };
  }
}
