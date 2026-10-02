/**
 * Labels = IMAP keywords (a.k.a. user flags).
 *
 * Dovecot stores keywords per mailbox, but most clients expose a
 * flat namespace so we read keywords across folders and union them.
 * For now `list` returns whatever keywords appear in INBOX; create
 * is a no-op (keywords spring into existence the first time they're
 * applied via modifyLabels).
 */

import { z } from 'zod';
import { router, protectedProcedure } from '../trpc';
import { withImap } from '../../lib/imap';

const colorSchema = z.object({
  backgroundColor: z.string(),
  textColor: z.string(),
});

type LabelColor = z.infer<typeof colorSchema>;

export interface Label {
  id: string;
  name: string;
  color: LabelColor | undefined;
  type: string;
}

export const labelsRouter = router({
  list: protectedProcedure.query(({ ctx }): Promise<Label[]> =>
    withImap(ctx.imap, async (client) => {
      const lock = await client.getMailboxLock('INBOX');
      try {
        const mb = client.mailbox;
        const flags =
          typeof mb === 'object' && mb && 'permanentFlags' in mb
            ? ((mb as { permanentFlags?: Iterable<string> }).permanentFlags ?? [])
            : [];
        return [...flags]
          .filter((f) => {
            // \Seen, \Flagged, \Deleted, etc. - IMAP system flags
            if (f.startsWith('\\')) return false;
            // \* = "any keyword allowed"
            if (f === '\\*') return false;
            // $Important, $Snoozed, $MDNSent, $Forwarded, $Junk, $NotJunk -
            // RFC 5788 / vendor-namespaced system keywords. Exposing them
            // as user labels in the sidebar is wrong: clicking $Important
            // tries to navigate to /mail/$Important, which the server can't
            // map to a real folder and silently falls back to INBOX.
            // $Important specifically already has its own /mail/important
            // virtual folder; $Snoozed has /mail/snoozed.
            if (f.startsWith('$')) return false;
            return true;
          })
          .map((name) => ({ id: name, name, color: undefined, type: 'user' }));
      } finally {
        lock.release();
      }
    }),
  ),

  create: protectedProcedure
    .input(z.object({ name: z.string().min(1), color: colorSchema.optional() }))
    .mutation(({ input }): Label => ({
      id: input.name,
      name: input.name,
      color: input.color ?? undefined,
      type: 'user',
    })),

  update: protectedProcedure
    .input(
      z.object({
        id: z.string(),
        name: z.string().optional(),
        color: colorSchema.optional(),
      }),
    )
    .mutation(({ input }): Label => ({
      id: input.id,
      name: input.name ?? input.id,
      color: input.color ?? undefined,
      type: 'user',
    })),

  delete: protectedProcedure
    .input(z.object({ id: z.string() }))
    .mutation(() => ({ ok: true })),
});
