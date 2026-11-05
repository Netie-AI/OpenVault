/**
 * Direct IMAP probe - bypasses Zero entirely.
 *
 * Connects to the mail server, finds the message by Message-Id, prints the
 * full envelope + body. If THIS hangs, the bug is in Dovecot/iRedMail config
 * (likely a stale or missing index for this user's mailbox). If THIS works
 * fast and Zero's /api/trpc/mail.get still hangs, the bug is in Zero.
 *
 * Run:
 *   cd apps/email/server && bun run scripts/fetch-thread-debug.ts
 *
 * Edit USER/PASS/HOST/TARGET_MESSAGE_ID below if you need to probe a
 * different account.
 */
import { ImapFlow } from 'imapflow';
import { simpleParser } from 'mailparser';

const USER = 'test@example.com';
const PASS = 'password';
const HOST = 'mail.example.com';
const PORT = 993;
const TARGET_MESSAGE_ID =
  '<your message id>';

const t = (label: string, start: number) =>
  console.log(`[t+${Math.round(performance.now() - start)}ms] ${label}`);

async function main() {
  const startedAt = performance.now();
  const client = new ImapFlow({
    host: HOST,
    port: PORT,
    secure: true,
    auth: { user: USER, pass: PASS },
    logger: false,
    socketTimeout: 60_000,
  });

  console.log(`connecting to ${HOST}:${PORT} as ${USER}…`);
  await client.connect();
  t('connected', startedAt);

  const lock = await client.getMailboxLock('INBOX');
  t('inbox locked', startedAt);

  try {
    const status = await client.status('INBOX', { messages: true, uidValidity: true });
    t(`status: ${status.messages} msgs, uidvalidity=${status.uidValidity}`, startedAt);

    // Try both forms - IMAP servers vary on whether they strip angle brackets
    // when indexing the Message-Id header.
    const variants = [
      TARGET_MESSAGE_ID,
      TARGET_MESSAGE_ID.replace(/^<|>$/g, ''),
    ];

    let uid: number | undefined;
    for (const v of variants) {
      const searchStart = performance.now();
      const hits = (await client.search({ header: { 'message-id': v } }, { uid: true })) as
        | number[]
        | false;
      const arr = Array.isArray(hits) ? hits : [];
      console.log(
        `[t+${Math.round(performance.now() - startedAt)}ms] search "${v.slice(0, 30)}…" ` +
          `→ ${arr.length} hit(s) in ${Math.round(performance.now() - searchStart)}ms`,
      );
      if (arr.length > 0) {
        uid = arr[arr.length - 1];
        break;
      }
    }

    if (!uid) {
      console.log('No message found by Message-Id. Listing recent ENVELOPE entries instead:');
      const recent = Math.max(1, (status.messages ?? 1) - 5);
      for await (const msg of client.fetch(`${recent}:*`, { envelope: true })) {
        console.log(`  uid=${msg.uid} subject=${JSON.stringify(msg.envelope?.subject)} msgId=${msg.envelope?.messageId}`);
      }
      return;
    }

    t(`uid resolved: ${uid}`, startedAt);

    const fetchStart = performance.now();
    const msg = await client.fetchOne(
      String(uid),
      { source: true, envelope: true, flags: true, internalDate: true, bodyStructure: true },
      { uid: true },
    );
    t(`fetchOne done in ${Math.round(performance.now() - fetchStart)}ms`, startedAt);

    if (!msg  || !('envelope' in msg)) {
      console.log('fetchOne returned nothing');
      return;
    }

    console.log('\n─── envelope ───');
    console.log(JSON.stringify(msg.envelope, null, 2));

    const sourceBuf = msg.source as Buffer | undefined;
    console.log(`\n─── source (${sourceBuf?.length ?? 0} bytes) ───`);
    if (sourceBuf) {
      const parsed = await simpleParser(sourceBuf);
      console.log('Subject :', parsed.subject);
      console.log('From    :', parsed.from?.text);
      console.log('To      :', parsed.to && Array.isArray(parsed.to) ? parsed.to.map((a) => a.text).join(', ') : (parsed.to as any)?.text);
      console.log('Date    :', parsed.date?.toISOString());
      console.log('\n─── text/plain (first 2 KB) ───');
      console.log((parsed.text ?? '').slice(0, 2048));
      if (!parsed.text && parsed.html) {
        console.log('\n─── text/html (first 2 KB) ───');
        console.log(
          (typeof parsed.html === 'string' ? parsed.html : '').slice(0, 2048),
        );
      }
    }
  } finally {
    lock.release();
    await client.logout().catch(() => undefined);
    client.close();
    t('done', startedAt);
  }
}

main().catch((err) => {
  console.error('FAILED:', err);
  process.exit(1);
});
