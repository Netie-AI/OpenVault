export const OPENVAULT_API =
  process.env.NEXT_PUBLIC_OPENVAULT_API || "http://127.0.0.1:5000";

export async function ovFetch(path: string, init?: RequestInit) {
  const url = path.startsWith("http")
    ? path
    : `${OPENVAULT_API}${path.startsWith("/") ? path : `/${path}`}`;
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  return res;
}
