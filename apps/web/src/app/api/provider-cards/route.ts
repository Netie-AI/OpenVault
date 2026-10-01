import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

import { NextResponse, type NextRequest } from "next/server";

import { validateBrowserMutationOrigin } from "@/server/authz/csrf";
import { isLoopbackHost } from "@/server/authz/routeGuard";

export const dynamic = "force-dynamic";

type AddBody = {
  label?: unknown;
  masked_id?: unknown;
  outcome?: unknown;
};

function scrub(text: string, secret: string): string {
  if (!secret || !text.includes(secret)) return text;
  return text.split(secret).join("");
}

function adminToken(): string {
  const override = (process.env.OPENVAULT_ADMIN_TOKEN_PATH || "").trim();
  const file = override
    ? override
    : path.join(
        (process.env.OPENVAULT_HOME || "").trim() || path.join(homedir(), ".openvault"),
        "admin_token",
      );
  return readFileSync(file, "utf8").trim();
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

function publicBody(data: AddBody | null, secret: string, fallback: string) {
  const label = scrub(typeof data?.label === "string" ? data.label : "", secret);
  const masked = scrub(typeof data?.masked_id === "string" ? data.masked_id : "", secret);
  const outcome = scrub(
    typeof data?.outcome === "string" ? data.outcome : fallback,
    secret,
  );
  return { label, masked_id: masked, outcome };
}

export function GET() {
  return new NextResponse(null, { status: 405, headers: { allow: "POST" } });
}

export async function POST(request: NextRequest) {
  if (!isLoopbackHost(request.nextUrl.hostname) || !validateBrowserMutationOrigin(request)) {
    return NextResponse.json(
      { label: "", masked_id: "", outcome: "unauthorized" },
      { status: 401 },
    );
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
  let token = "";
  try {
    token = adminToken();
  } catch {
    token = "";
  }
  if (!origin || !token) {
    return NextResponse.json(
      { label: "", masked_id: "", outcome: "unauthorized" },
      { status: 401 },
    );
  }

  try {
    const upstream = await fetch(`${origin}/api/keys/cards`, {
      method: "POST",
      redirect: "manual",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
        "X-OpenVault-Admin": token,
      },
      body: JSON.stringify({ provider, secret }),
      signal: AbortSignal.timeout(30_000),
    });
    const data = (await upstream.json().catch(() => null)) as AddBody | null;
    const status = upstream.status >= 200 && upstream.status < 600 ? upstream.status : 502;
    return NextResponse.json(publicBody(data, secret, "probe failed"), { status });
  } catch {
    return NextResponse.json(
      publicBody(null, secret, "test call failed (unreachable)"),
      { status: 502 },
    );
  }
}
