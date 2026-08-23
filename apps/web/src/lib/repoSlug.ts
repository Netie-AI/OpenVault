/**
 * Encoding/decoding for the repository slug that identifies what the Ship
 * wizard is deploying: a GitHub repo, a local folder, an upload session, or an
 * existing project.
 *
 * base64url (URL-safe base64) so the value survives a route segment untouched.
 *
 * The vendor original ran on `Buffer`, which is a Node global — these helpers
 * run in the browser, so the transcoding goes through TextEncoder/TextDecoder
 * instead of `btoa(str)` directly: a local path may hold non-Latin-1 characters
 * (`D:\проекты\app`) and raw `btoa` throws `InvalidCharacterError` on those.
 */

const LOCAL_PREFIX = "local:";
const UPLOAD_PREFIX = "upload:";
const REPO_V2_PREFIX = "repo:v2:";
const PROJECT_PREFIX = "project:";

export type DecodedSlug =
  | { kind: "repo"; owner: string; repo: string; branch?: string; projectId?: string }
  | { kind: "local"; path: string }
  | { kind: "upload"; sessionId: string }
  | { kind: "project"; projectId: string };

function encodeBase64Url(data: string): string {
  const bytes = new TextEncoder().encode(data);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}

function decodeBase64Url(slug: string): string {
  let base64 = slug.replace(/-/g, "+").replace(/_/g, "/");
  while (base64.length % 4) base64 += "=";

  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

/** Encodes owner and repo into a URL-safe base64 slug. */
export function encodeRepoSlug(owner: string, repo: string): string {
  return encodeBase64Url(`${owner}/${repo}`);
}

/**
 * Encodes a repo plus the branch/project it is pinned to. The bare
 * `owner/repo` form above cannot carry either.
 */
export function encodeRepoSlugV2(payload: {
  owner: string;
  repo: string;
  branch?: string;
  projectId?: string;
}): string {
  return encodeBase64Url(REPO_V2_PREFIX + JSON.stringify(payload));
}

/** Encodes a local path into a URL-safe base64 slug (prefixed with "local:"). */
export function encodeLocalSlug(path: string): string {
  return encodeBase64Url(LOCAL_PREFIX + path);
}

/**
 * Encodes a folder-upload session id into a URL-safe slug (prefixed "upload:").
 * The deploy wizard decodes it and re-fetches the scan for that session.
 */
export function encodeUploadSlug(sessionId: string): string {
  return encodeBase64Url(UPLOAD_PREFIX + sessionId);
}

/**
 * Encodes an existing project id into a URL-safe slug (prefixed "project:").
 * The deploy wizard decodes it and hydrates from the project's saved config —
 * used by any repo-less project that deploys without a clone step.
 */
export function encodeProjectSlug(projectId: string): string {
  return encodeBase64Url(PROJECT_PREFIX + projectId);
}

/** Decodes a slug back to a repo, local path, upload session, or project. */
export function decodeSlug(slug: string): DecodedSlug | null {
  try {
    const decoded = decodeBase64Url(slug);

    if (decoded.startsWith(LOCAL_PREFIX)) {
      const path = decoded.slice(LOCAL_PREFIX.length);
      return path ? { kind: "local", path } : null;
    }

    if (decoded.startsWith(UPLOAD_PREFIX)) {
      const sessionId = decoded.slice(UPLOAD_PREFIX.length);
      return sessionId ? { kind: "upload", sessionId } : null;
    }

    if (decoded.startsWith(PROJECT_PREFIX)) {
      const projectId = decoded.slice(PROJECT_PREFIX.length);
      return projectId ? { kind: "project", projectId } : null;
    }

    if (decoded.startsWith(REPO_V2_PREFIX)) {
      const payload = JSON.parse(decoded.slice(REPO_V2_PREFIX.length));
      if (!payload || typeof payload !== "object") return null;

      const { owner, repo, branch, projectId } = payload as Record<string, unknown>;
      if (typeof owner !== "string" || typeof repo !== "string" || !owner || !repo) {
        return null;
      }

      return {
        kind: "repo",
        owner,
        repo,
        ...(typeof branch === "string" && branch ? { branch } : {}),
        ...(typeof projectId === "string" && projectId ? { projectId } : {}),
      };
    }

    const [owner, repo] = decoded.split("/");
    if (!owner || !repo) return null;
    return { kind: "repo", owner, repo };
  } catch {
    return null;
  }
}

/**
 * Extracts owner and repo from a GitHub URL.
 * Handles `https://github.com/o/r`, `…/r.git`, and `git@github.com:o/r.git`.
 */
export function extractOwnerRepoFromUrl(url: string): { owner: string; repo: string } | null {
  // Allow dots in the repo name, optionally stripping a `.git` suffix.
  const httpsMatch = url.match(/github\.com\/([^/]+)\/(.+?)(?:\.git)?$/);
  if (httpsMatch) {
    return { owner: httpsMatch[1], repo: httpsMatch[2] };
  }

  const sshMatch = url.match(/github\.com:([^/]+)\/(.+?)(?:\.git)?$/);
  if (sshMatch) {
    return { owner: sshMatch[1], repo: sshMatch[2] };
  }

  return null;
}
