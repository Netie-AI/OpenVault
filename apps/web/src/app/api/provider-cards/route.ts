import { NextResponse, type NextRequest } from "next/server";

import { validateBrowserMutationOrigin } from "@/server/authz/csrf";
import { isLoopbackHost } from "@/server/authz/routeGuard";

export const dynamic = "force-dynamic";

const ADMIN_HEADER = "x-openvault-admin";

type AddBody = {
  label?: unknown;
  masked_id?: unknown;
  outcome?: unknown;
};

function scrub(text: string, hidden: string): string {
  if (!hidden || !text.includes(hidden)) return text;
  return text.split(hidden).join("");
}

function apiOrigin(): string | null {
  const raw = (process.env.OPENVAULT_API || "http://127.0.0.1:5000").trim();
  try {
    const url = new URL(raw);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (!isLoopbackHost(url.hostname)) return null;
    return url.origin;
  } catch {
    return null;
  }
}

function publicBody(
  data: AddBody | null,
  secret: string,
  admin: string,
  fallback: string,
) {
  const hide = (value: string) => scrub(scrub(value, secret), admin);
  const label = hide(typeof data?.label === "string" ? data.label : "");
  const masked = hide(typeof data?.masked_id === "string" ? data.masked_id : "");
  const outcome = hide(typeof data?.outcome === "string" ? data.outcome : fallback);
  return { label, masked_id: masked, outcome };
}

function unauthorized() {
  return NextResponse.json(
    { label: "", masked_id: "", outcome: "unauthorized" },
    { status: 401 },
  );
}

export function GET() {
  return new NextResponse(null, { status: 405, headers: { allow: "POST" } });
}

export async function POST(request: NextRequest) {
  if (!validateBrowserMutationOrigin(request)) {
    return unauthorized();
  }
  const presented = request.headers.get(ADMIN_HEADER);
  if (presented === null || presented.trim() === "") {
    return unauthorized();
  }

  let provider = "";
  let secret = "";
  try {
    const body = (await request.json()) as { provider?: unknown; secret?: unknown };
    if (typeof body?.provider === "string") provider = body.provider;
    if (typeof body?.secret === "string") secret = body.secret;
  } catch {
    return NextResponse.json(
      { label: "", masked_id: "", outcome: "no key entered" },
      { status: 400 },
    );
  }

  const origin = apiOrigin();
  if (!origin) {
    return unauthorized();
  }

  try {
    const upstream = await fetch(`${origin}/api/keys/cards`, {
      method: "POST",
      redirect: "manual",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
        "X-OpenVault-Admin": presented,
      },
      body: JSON.stringify({ provider, secret }),
      signal: AbortSignal.timeout(30_000),
    });
    const data = (await upstream.json().catch(() => null)) as AddBody | null;
    const status = upstream.status >= 200 && upstream.status < 600 ? upstream.status : 502;
    return NextResponse.json(publicBody(data, secret, presented, "probe failed"), { status });
  } catch {
    return NextResponse.json(
      publicBody(null, secret, presented, "test call failed (unreachable)"),
      { status: 502 },
    );
  }
}
