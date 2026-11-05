/**
 * `user.*` - identity and account-level operations.
 *
 * In the new model the session row is the user. `me` reads from
 * ctx.session; `delete` wipes session + prefs + templates +
 * cookie_consent for that email (no IMAP deletion - that's an admin
 * panel responsibility, behind a separate auth boundary).
 */

import { eq } from 'drizzle-orm';
import { router, protectedProcedure } from '../trpc';
import { db, schema } from '../../db';
import { deleteSession } from '../../lib/session';

export const userRouter = router({
  me: protectedProcedure.query(({ ctx }) => ({
    email: ctx.session.email,
    name: ctx.session.name,
    imapHost: ctx.session.imapHost,
    smtpHost: ctx.session.smtpHost,
  })),

  delete: protectedProcedure.mutation(async ({ ctx }) => {
    const email = ctx.session.email;
    await Promise.all([
      deleteSession(ctx.session.sessionId),
      db.delete(schema.userSettings).where(eq(schema.userSettings.email, email)),
      db.delete(schema.userHotkeys).where(eq(schema.userHotkeys.email, email)),
      db.delete(schema.emailTemplate).where(eq(schema.emailTemplate.email, email)),
      db.delete(schema.cookieConsent).where(eq(schema.cookieConsent.email, email)),
    ]);
    return { ok: true, success: true, message: 'Account deleted' };
  }),

  // Kept for client compatibility. Intercom is a SaaS thing we stripped
  // out - return an empty token so the client's chat widget no-ops.
  getIntercomToken: protectedProcedure.query(() => ({ token: '' })),
});
