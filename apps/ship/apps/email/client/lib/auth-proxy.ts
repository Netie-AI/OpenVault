/**
 * Server-side auth helper. Used by React Router loaders to fetch the
 * current session inside SSR.
 *
 * Hits the new `/auth/session` endpoint and reshapes the response to
 * match what consumers expect (`{ user: { id, email, name, image } }`).
 */

import { BACKEND_URL } from './backend-url';

const BASE = BACKEND_URL;

interface ServerSession {
  email: string;
  name: string | null;
  expiresAt: string;
}

export interface ProxiedSession {
  user: {
    id: string;
    email: string;
    name: string;
    image: string | null;
  };
  expiresAt: string;
}

async function getSession({ headers }: { headers: Headers }): Promise<ProxiedSession | null> {
  try {
    const res = await fetch(`${BASE}/auth/session`, {
      headers,
      credentials: 'include',
    });
    if (!res.ok) return null;
    const body = (await res.json()) as ServerSession | null;
    if (!body) return null;
    return {
      user: {
        id: body.email,
        email: body.email,
        name: body.name ?? body.email,
        image: null,
      },
      expiresAt: body.expiresAt,
    };
  } catch (err) {
    console.error('Failed to get session:', err);
    return null;
  }
}

export const authProxy = {
  api: { getSession },
};
