/**
 * User settings - read/write the `user_settings` row keyed by email.
 *
 * The settings JSON is owned end-to-end by the client; the server
 * just round-trips it. Defaults from `lib/schemas` fill in any new
 * keys we add over time.
 */

import { eq } from 'drizzle-orm';
import { router, protectedProcedure } from '../trpc';
import { db, schema } from '../../db';
import { userSettingsSchema, defaultUserSettings } from '../../lib/schemas';

export const settingsRouter = router({
  get: protectedProcedure.query(async ({ ctx }) => {
    const row = await db.query.userSettings.findFirst({
      where: eq(schema.userSettings.email, ctx.session.email),
    });
    return { settings: { ...defaultUserSettings, ...(row?.settings ?? {}) } };
  }),

  save: protectedProcedure
    .input(userSettingsSchema.partial())
    .mutation(async ({ ctx, input }) => {
      const now = new Date();
      const existing = await db.query.userSettings.findFirst({
        where: eq(schema.userSettings.email, ctx.session.email),
      });
      const merged = { ...defaultUserSettings, ...(existing?.settings ?? {}), ...input };
      if (existing) {
        await db
          .update(schema.userSettings)
          .set({ settings: merged, updatedAt: now })
          .where(eq(schema.userSettings.email, ctx.session.email));
      } else {
        await db.insert(schema.userSettings).values({
          email: ctx.session.email,
          settings: merged,
          createdAt: now,
          updatedAt: now,
        });
      }
      return { settings: merged, success: true };
    }),
});
