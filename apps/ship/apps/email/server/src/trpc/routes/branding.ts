/**
 * Branding - read-only via tRPC.
 *
 * `get` is public because the login page renders pre-auth. Writes used
 * to live here as a public mutation, which let anyone with reach to the
 * Zero server rewrite the login page. Removed: writes now go through
 * `PATCH /admin/branding` with a shared admin token (see
 * [branding-admin.ts](../../routes/branding-admin.ts)). Openship is the
 * only caller; the token never reaches the browser.
 */

import { router, publicProcedure } from '../trpc';
import { getBranding } from '../../lib/branding';

export const brandingRouter = router({
  get: publicProcedure.query(() => getBranding()),
});
