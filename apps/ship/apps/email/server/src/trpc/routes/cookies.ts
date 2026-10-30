/**
 * Cookie consent + locale cookie.
 *
 * `setLocaleCookie` is here (not in user.ts) because the client wires
 * it to the language picker and expects it under `cookiePreferences`.
 */

import { z } from 'zod';
import { eq } from 'drizzle-orm';
import { router, publicProcedure, protectedProcedure } from '../trpc';
import { db, schema } from '../../db';
import { defaultCookieConsent } from '../../lib/cookies';

export const cookiePreferencesRouter = router({
  get: protectedProcedure.query(async ({ ctx }) => {
    const row = await db.query.cookieConsent.findFirst({
      where: eq(schema.cookieConsent.email, ctx.session.email),
    });
    return row?.consent ?? defaultCookieConsent;
  }),

  save: protectedProcedure
    .input(
      z.object({
        necessary: z.boolean(),
        analytics: z.boolean(),
        marketing: z.boolean(),
        preferences: z.boolean(),
      }),
    )
    .mutation(async ({ ctx, input }) => {
      const now = new Date();
      const existing = await db.query.cookieConsent.findFirst({
        where: eq(schema.cookieConsent.email, ctx.session.email),
      });
      if (existing) {
        await db
          .update(schema.cookieConsent)
          .set({ consent: input, updatedAt: now })
          .where(eq(schema.cookieConsent.email, ctx.session.email));
      } else {
        await db.insert(schema.cookieConsent).values({
          email: ctx.session.email,
          consent: input,
          updatedAt: now,
        });
      }
      return input;
    }),

  setLocaleCookie: publicProcedure
    .input(z.object({ locale: z.string() }))
    .mutation(({ input }) => ({ ok: true, locale: input.locale })),
});
