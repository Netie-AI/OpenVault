/**
 * Loopback WebAuthn for vault unseal (Windows Hello / Touch ID / optional iPhone).
 *
 * Not autofill. Not a login agent. The authenticator PRF wraps the live master
 * key; passphrase on disk stays the backup.
 */

import { apiPost } from "./client";
import type { VaultStatus } from "./secrets";

export function webauthnAvailable(): boolean {
  return typeof window !== "undefined" && typeof window.PublicKeyCredential === "function";
}

function b64urlToBuf(text: string): ArrayBuffer {
  const pad = "=".repeat((4 - (text.length % 4)) % 4);
  const bin = atob(text.replace(/-/g, "+").replace(/_/g, "/") + pad);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}

function bufToB64url(buf: BufferSource): string {
  const bytes =
    buf instanceof ArrayBuffer
      ? new Uint8Array(buf)
      : new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function prfFirst(results: unknown): ArrayBuffer | null {
  if (!results || typeof results !== "object") return null;
  const prf = (results as { prf?: { results?: { first?: BufferSource } } }).prf;
  const first = prf?.results?.first;
  if (!first) return null;
  return first instanceof ArrayBuffer
    ? first
    : new Uint8Array(first.buffer, first.byteOffset, first.byteLength).slice().buffer;
}

type CreatePublicKey = {
  challenge: string;
  user: { id: string; name: string; displayName: string };
  extensions?: { prf?: { eval?: { first: string } } };
} & Record<string, unknown>;

type RequestPublicKey = {
  challenge: string;
  allowCredentials?: Array<{ type: string; id: string; transports?: string[] }>;
  extensions?: { prf?: { eval?: { first: string } } };
} & Record<string, unknown>;

function decodeCreateOptions(publicKey: CreatePublicKey): PublicKeyCredentialCreationOptions {
  const extFirst = publicKey.extensions?.prf?.eval?.first;
  return {
    ...(publicKey as unknown as PublicKeyCredentialCreationOptions),
    challenge: b64urlToBuf(publicKey.challenge),
    user: {
      ...publicKey.user,
      id: b64urlToBuf(publicKey.user.id),
    },
    extensions: extFirst
      ? { prf: { eval: { first: b64urlToBuf(extFirst) } } }
      : publicKey.extensions,
  } as PublicKeyCredentialCreationOptions;
}

function decodeRequestOptions(publicKey: RequestPublicKey): PublicKeyCredentialRequestOptions {
  const extFirst = publicKey.extensions?.prf?.eval?.first;
  return {
    ...(publicKey as unknown as PublicKeyCredentialRequestOptions),
    challenge: b64urlToBuf(publicKey.challenge),
    allowCredentials: (publicKey.allowCredentials ?? []).map((cred) => ({
      type: "public-key" as const,
      id: b64urlToBuf(cred.id),
      transports: cred.transports as AuthenticatorTransport[] | undefined,
    })),
    extensions: extFirst
      ? { prf: { eval: { first: b64urlToBuf(extFirst) } } }
      : publicKey.extensions,
  } as PublicKeyCredentialRequestOptions;
}

function ceremonyError(err: unknown): Error {
  if (err instanceof Error && err.name === "NotAllowedError") {
    return new Error("Passkey was cancelled, or this device has no Windows Hello / Touch ID.");
  }
  if (err instanceof Error) return err;
  return new Error("Passkey ceremony failed");
}

export async function registerVaultPasskey(hybrid: boolean): Promise<VaultStatus> {
  const begin = await apiPost<{ session_id: string; publicKey: CreatePublicKey }>(
    "/api/vault/webauthn/register/begin",
    { hybrid },
  );
  let cred: Credential | null;
  try {
    cred = await navigator.credentials.create({
      publicKey: decodeCreateOptions(begin.publicKey),
    });
  } catch (err) {
    throw ceremonyError(err);
  }
  if (!(cred instanceof PublicKeyCredential)) {
    throw new Error("no passkey created");
  }
  const att = cred.response as AuthenticatorAttestationResponse;
  const spki = att.getPublicKey?.();
  if (!spki) {
    throw new Error("authenticator did not return a public key");
  }
  const first = prfFirst(cred.getClientExtensionResults());
  if (!first) {
    throw new Error(
      "this authenticator did not return a PRF secret. Use Windows Hello / Touch ID, or passphrase. iPhone: try the iPhone button.",
    );
  }
  return apiPost<VaultStatus>("/api/vault/webauthn/register/finish", {
    session_id: begin.session_id,
    credential_id: bufToB64url(cred.rawId),
    public_key_spki: bufToB64url(spki),
    client_data_b64: bufToB64url(att.clientDataJSON),
    extensions: { prf: { results: { first: bufToB64url(first) } } },
  });
}

export async function unsealVaultWithPasskey(): Promise<VaultStatus> {
  const begin = await apiPost<{ session_id: string; publicKey: RequestPublicKey }>(
    "/api/vault/webauthn/unseal/begin",
  );
  let cred: Credential | null;
  try {
    cred = await navigator.credentials.get({
      publicKey: decodeRequestOptions(begin.publicKey),
    });
  } catch (err) {
    throw ceremonyError(err);
  }
  if (!(cred instanceof PublicKeyCredential)) {
    throw new Error("no passkey assertion");
  }
  const assertion = cred.response as AuthenticatorAssertionResponse;
  const first = prfFirst(cred.getClientExtensionResults());
  if (!first) {
    throw new Error(
      "this authenticator did not return a PRF secret. Use passphrase this time.",
    );
  }
  return apiPost<VaultStatus>("/api/vault/webauthn/unseal/finish", {
    session_id: begin.session_id,
    credential_id: bufToB64url(cred.rawId),
    client_data_b64: bufToB64url(assertion.clientDataJSON),
    authenticator_data_b64: bufToB64url(assertion.authenticatorData),
    signature_b64: bufToB64url(assertion.signature),
    extensions: { prf: { results: { first: bufToB64url(first) } } },
  });
}

export function clearVaultPasskey(): Promise<VaultStatus> {
  return apiPost<VaultStatus>("/api/vault/webauthn/clear");
}
