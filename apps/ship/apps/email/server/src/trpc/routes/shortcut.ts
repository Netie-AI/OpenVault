/**
 * Per-user hotkey overrides on top of `defaultShortcuts`.
 *
 * The client renders the merged list; we just persist diffs against
 * the defaults.
 */

import { z } from 'zod';
import { and, eq } from 'drizzle-orm';
import { router, protectedProcedure } from '../trpc';
import { db, schema } from '../../db';
import { defaultShortcuts, type ShortcutEntry } from '../../lib/shortcuts';

export const shortcutRouter = router({
  list: protectedProcedure.query(async ({ ctx }) => {
    const rows = await db
      .select()
      .from(schema.userHotkeys)
      .where(eq(schema.userHotkeys.email, ctx.session.email));
    const overrides = new Map<string, ShortcutEntry>();
    for (const r of rows) overrides.set(r.shortcutId, r.payload);
    return defaultShortcuts.map((s) => overrides.get(s.id) ?? s);
  }),

  save: protectedProcedure
    .input(
      z.object({
        id: z.string(),
        keys: z.array(z.string()).nullable(),
      }),
    )
    .mutation(async ({ ctx, input }) => {
      const base = defaultShortcuts.find((s) => s.id === input.id);
      if (!base) return { ok: false };
      const payload: ShortcutEntry = {
        ...base,
        override: input.keys,
      };
      const now = new Date();
      const existing = await db.query.userHotkeys.findFirst({
        where: and(
          eq(schema.userHotkeys.email, ctx.session.email),
          eq(schema.userHotkeys.shortcutId, input.id),
        ),
      });
      if (existing) {
        await db
          .update(schema.userHotkeys)
          .set({ payload, updatedAt: now })
          .where(
            and(
              eq(schema.userHotkeys.email, ctx.session.email),
              eq(schema.userHotkeys.shortcutId, input.id),
            ),
          );
      } else {
        await db.insert(schema.userHotkeys).values({
          email: ctx.session.email,
          shortcutId: input.id,
          payload,
          updatedAt: now,
        });
      }
      return { ok: true };
    }),

  reset: protectedProcedure.mutation(async ({ ctx }) => {
    await db.delete(schema.userHotkeys).where(eq(schema.userHotkeys.email, ctx.session.email));
    return { ok: true };
  }),
});
