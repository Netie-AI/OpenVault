/**
 * Saved compose templates - straightforward CRUD over `email_template`.
 */

import { z } from 'zod';
import { and, eq } from 'drizzle-orm';
import { nanoid } from 'nanoid';
import { router, protectedProcedure } from '../trpc';
import { db, schema } from '../../db';

export const templatesRouter = router({
  list: protectedProcedure.query(async ({ ctx }) => {
    const templates = await db
      .select()
      .from(schema.emailTemplate)
      .where(eq(schema.emailTemplate.email, ctx.session.email));
    return { templates };
  }),

  create: protectedProcedure
    .input(
      z.object({
        name: z.string().min(1),
        subject: z.string().nullable().optional(),
        body: z.string().nullable().optional(),
        to: z.array(z.string()).nullable().optional(),
        cc: z.array(z.string()).nullable().optional(),
        bcc: z.array(z.string()).nullable().optional(),
      }),
    )
    .mutation(async ({ ctx, input }) => {
      const now = new Date();
      const id = nanoid(20);
      await db.insert(schema.emailTemplate).values({
        id,
        email: ctx.session.email,
        name: input.name,
        subject: input.subject ?? null,
        body: input.body ?? null,
        to: input.to ?? null,
        cc: input.cc ?? null,
        bcc: input.bcc ?? null,
        createdAt: now,
        updatedAt: now,
      });
      return { id };
    }),

  delete: protectedProcedure
    .input(z.object({ id: z.string() }))
    .mutation(async ({ ctx, input }) => {
      await db
        .delete(schema.emailTemplate)
        .where(
          and(
            eq(schema.emailTemplate.id, input.id),
            eq(schema.emailTemplate.email, ctx.session.email),
          ),
        );
      return { ok: true };
    }),
});
