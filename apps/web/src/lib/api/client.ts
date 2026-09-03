/**
 * The single place an HTTP request leaves the OpenVault UI.
 *
 * Pages currently call `fetch()` raw against `http://127.0.0.1:5000`, which
 * bypasses the dev-server rewrite, has no timeout, no abort plumbing, and turns
 * a 404 into a silent `undefined`. Everything here exists to make those four
 * failures impossible: one base-URL rule, one timeout policy, one place where a
 * non-2xx becomes a typed `ApiError`.
 *
 * Base URL — in the browser we go through the `/ov-api` rewrite declared in
 * `next.config.mjs` (which proxies to `OPENVAULT_API`, default
 * `http://127.0.0.1:5000`). Same-origin means no CORS preflight and no hardcoded
 * port in the bundle. On the server the rewrite does not exist, so we hit the
 * backend origin directly using the same env var the rewrite reads.
 */

/** Where a request went wrong. `http` means the server answered and refused. */
export type ApiFailureKind = "http" | "network" | "timeout" | "aborted" | "parse";

/** Browser path prefix; matches the `/ov-api/:path*` rewrite in next.config.mjs. */
export const BROWSER_API_PREFIX = "/ov-api";

/** Backend origin used for server-side rendering, where rewrites do not apply. */
export const SERVER_API_ORIGIN =
  (typeof process !== "undefined" && process.env?.OPENVAULT_API) || "http://127.0.0.1:5000";

/** Most OpenVault routes are local and fast; 15s is already generous. */
export const DEFAULT_TIMEOUT_MS = 15_000;

/**
 * For routes that shell out, clone, probe every provider, or block on a native
 * dialog. Never apply this by default — a hung request that looks alive for
 * five minutes is worse UX than a clean timeout.
 */
export const LONG_TIMEOUT_MS = 180_000;

export type QueryPrimitive = string | number | boolean | null | undefined;
export type QueryParams = Record<string, QueryPrimitive | QueryPrimitive[]>;

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  /** Serialized as JSON. Use `undefined` for no body (not `null`). */
  body?: unknown;
  query?: QueryParams;
  /** Caller's cancellation signal. Combined with the timeout, not replaced by it. */
  signal?: AbortSignal;
  /** Milliseconds. `0` disables the timeout entirely (use with a caller signal). */
  timeoutMs?: number;
  headers?: Record<string, string>;
}

/**
 * Every non-2xx, network drop, timeout and unparseable body arrives here.
 * `kind` tells a page whether to offer "retry" (network/timeout) or to show the
 * server's own message (http).
 */
export class ApiError extends Error {
  readonly kind: ApiFailureKind;
  /** HTTP status, or 0 when the request never got an answer. */
  readonly status: number;
  readonly method: string;
  readonly url: string;
  /** FastAPI's `detail` field, verbatim — string, object, or validation array. */
  readonly detail: unknown;
  /** The parsed (or raw text) response body, for debugging surfaces. */
  readonly body: unknown;

  constructor(args: {
    kind: ApiFailureKind;
    status: number;
    method: string;
    url: string;
    message: string;
    detail?: unknown;
    body?: unknown;
  }) {
    super(args.message);
    this.name = "ApiError";
    this.kind = args.kind;
    this.status = args.status;
    this.method = args.method;
    this.url = args.url;
    this.detail = args.detail;
    this.body = args.body;
  }
}

export function isApiError(err: unknown): err is ApiError {
  return err instanceof ApiError;
}

/** 404 is a normal answer for "no such deploy plan / key / session". */
export function isNotFound(err: unknown): boolean {
  return isApiError(err) && err.status === 404;
}

/** The backend refused the request body (FastAPI 422 or an explicit 400). */
export function isBadRequest(err: unknown): boolean {
  return isApiError(err) && (err.status === 400 || err.status === 422);
}

function encodeQuery(query: QueryParams | undefined): string {
  if (!query) return "";
  const parts = new URLSearchParams();
  for (const [key, raw] of Object.entries(query)) {
    const values = Array.isArray(raw) ? raw : [raw];
    for (const value of values) {
      if (value === undefined || value === null) continue;
      parts.append(key, String(value));
    }
  }
  const encoded = parts.toString();
  return encoded ? `?${encoded}` : "";
}

/**
 * Absolute-or-prefixed URL for a backend path. Exported because SSE and
 * `EventSource` need the URL without going through `apiFetch`.
 */
export function ovUrl(path: string, query?: QueryParams): string {
  if (/^https?:\/\//i.test(path)) return `${path}${encodeQuery(query)}`;
  const suffix = path.startsWith("/") ? path : `/${path}`;
  const base = typeof window === "undefined" ? SERVER_API_ORIGIN : BROWSER_API_PREFIX;
  return `${base}${suffix}${encodeQuery(query)}`;
}

interface LinkedSignal {
  signal: AbortSignal;
  timedOut: () => boolean;
  dispose: () => void;
}

/**
 * Combine the caller's signal with our timeout.
 *
 * `AbortSignal.any` would do this in one line but is not in every TS DOM lib we
 * build against, and a silently-missing global here would mean requests that
 * never abort. Wiring the listeners by hand is boring and portable.
 */
function linkSignals(caller: AbortSignal | undefined, timeoutMs: number): LinkedSignal {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let didTimeout = false;

  const onCallerAbort = () => controller.abort();

  if (caller) {
    if (caller.aborted) controller.abort();
    else caller.addEventListener("abort", onCallerAbort, { once: true });
  }
  if (timeoutMs > 0 && !controller.signal.aborted) {
    timer = setTimeout(() => {
      didTimeout = true;
      controller.abort();
    }, timeoutMs);
  }

  return {
    signal: controller.signal,
    timedOut: () => didTimeout,
    dispose: () => {
      if (timer !== undefined) clearTimeout(timer);
      caller?.removeEventListener("abort", onCallerAbort);
    },
  };
}

function messageFromDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc, msg, type}, ...]
    const msgs = detail
      .map((item) =>
        item && typeof item === "object" && "msg" in item
          ? String((item as { msg: unknown }).msg)
          : "",
      )
      .filter(Boolean);
    if (msgs.length) return msgs.join("; ");
  }
  if (detail && typeof detail === "object") {
    const obj = detail as Record<string, unknown>;
    for (const key of ["message", "error", "reason", "detail"]) {
      const value = obj[key];
      if (typeof value === "string" && value.trim()) return value;
    }
  }
  return fallback;
}

/** Pull the human message out of whatever error envelope the backend used. */
function describeFailure(body: unknown, status: number, statusText: string): {
  message: string;
  detail: unknown;
} {
  const fallback = `HTTP ${status}${statusText ? ` ${statusText}` : ""}`;
  if (body && typeof body === "object") {
    const obj = body as Record<string, unknown>;
    // FastAPI HTTPException, then the OpenAI-shaped envelope /v1 and the
    // ratelimit guard use.
    if ("detail" in obj) {
      return { message: messageFromDetail(obj.detail, fallback), detail: obj.detail };
    }
    if ("error" in obj) {
      return { message: messageFromDetail(obj.error, fallback), detail: obj.error };
    }
  }
  if (typeof body === "string" && body.trim()) {
    return { message: `${fallback}: ${body.slice(0, 400)}`, detail: body };
  }
  return { message: fallback, detail: undefined };
}

/**
 * Perform one request and return the decoded JSON body.
 *
 * Throws `ApiError` for anything that is not a 2xx with a decodable body. A
 * 204/empty response resolves to `undefined` cast to `T` — only call such
 * endpoints with `T = void`.
 */
export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const url = ovUrl(path, options.query);
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const link = linkSignals(options.signal, timeoutMs);

  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  let payload: string | undefined;
  if (options.body !== undefined) {
    payload = JSON.stringify(options.body);
    headers["Content-Type"] = headers["Content-Type"] ?? "application/json";
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: payload,
      signal: link.signal,
      // The console reflects live machine state; a cached vault listing is a bug.
      cache: "no-store",
    });
  } catch (err) {
    if (link.timedOut()) {
      throw new ApiError({
        kind: "timeout",
        status: 0,
        method,
        url,
        message: `${method} ${url} timed out after ${timeoutMs}ms`,
      });
    }
    if (options.signal?.aborted) {
      throw new ApiError({
        kind: "aborted",
        status: 0,
        method,
        url,
        message: `${method} ${url} was cancelled`,
      });
    }
    throw new ApiError({
      kind: "network",
      status: 0,
      method,
      url,
      message:
        err instanceof Error
          ? `${method} ${url} failed: ${err.message}`
          : `${method} ${url} failed`,
      body: err,
    });
  } finally {
    link.dispose();
  }

  const text = await response.text();
  let parsed: unknown = undefined;
  let parseFailed = false;
  if (text.length > 0) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
      parseFailed = true;
    }
  }

  if (!response.ok) {
    const { message, detail } = describeFailure(parsed, response.status, response.statusText);
    throw new ApiError({
      kind: "http",
      status: response.status,
      method,
      url,
      message,
      detail,
      body: parsed,
    });
  }

  if (parseFailed) {
    throw new ApiError({
      kind: "parse",
      status: response.status,
      method,
      url,
      message: `${method} ${url} returned a non-JSON body`,
      body: parsed,
    });
  }

  return parsed as T;
}

/** GET with optional query string. */
export function apiGet<T>(
  path: string,
  options: Omit<RequestOptions, "method" | "body"> = {},
): Promise<T> {
  return apiFetch<T>(path, { ...options, method: "GET" });
}

export function apiPost<T>(
  path: string,
  body?: unknown,
  options: Omit<RequestOptions, "method" | "body"> = {},
): Promise<T> {
  return apiFetch<T>(path, { ...options, method: "POST", body });
}

export function apiPut<T>(
  path: string,
  body?: unknown,
  options: Omit<RequestOptions, "method" | "body"> = {},
): Promise<T> {
  return apiFetch<T>(path, { ...options, method: "PUT", body });
}

export function apiPatch<T>(
  path: string,
  body?: unknown,
  options: Omit<RequestOptions, "method" | "body"> = {},
): Promise<T> {
  return apiFetch<T>(path, { ...options, method: "PATCH", body });
}

export function apiDelete<T>(
  path: string,
  options: Omit<RequestOptions, "method" | "body"> = {},
): Promise<T> {
  return apiFetch<T>(path, { ...options, method: "DELETE" });
}
