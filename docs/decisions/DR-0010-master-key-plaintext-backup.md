---
status: proposed
date: 2026-08-20
decision-makers: founder
---

# DR-0010 - The plaintext master key backup is visible, retirable, and never silent

## Context and Problem Statement

`_migrate_plaintext_key` (`OpenMW/openmw/openvault/vault/crypto.py:61-82`) writes
`master.key.v0.bak` when it upgrades a legacy v0 vault. That file is the raw, unwrapped
Fernet master key, and the function leaves it on disk permanently. The docstring states
the reason plainly: an unreadable vault is a worse outcome than a leftover file, and the
backup is the only recovery path if the wrapped key later fails to unwrap.

That reasoning is sound and it is not the thing being overturned here. What is being
overturned is that the trade is invisible.

**The backup is a full bypass of the passphrase.** Confirmed by reading the load path,
not inferred: `keywrap.is_wrapped` (`vault/keywrap.py:263`) is a check for the four-byte
`OVK1` magic, and a 44-byte raw key does not carry it. So `Seal.__init__`
(`crypto.py:132`) takes the not-wrapped branch, `_load_or_create_master_key` takes its
legacy branch, and `blob.strip()` becomes the Fernet key directly. Renaming
`master.key.v0.bak` to `master.key` inside a copied vault folder opens every sealed row
in `keys.db`, on any machine, under any account, with no passphrase and no DPAPI.

This falsifies a stated PRD success assertion verbatim. PRD-001 section 4 says: "WHEN I
copy the vault folder to another account/machine without unwrap rights, THE SYSTEM SHALL
make the ciphertext unusable." On any vault that has been through a v0 migration, that
assertion is false today.

Three further facts shape the decision rather than merely describing the defect:

1. **It is a tested contract, not an oversight.** `OpenMW/tests/test_keywrap.py:142`
   asserts the backup must exist; `:180` asserts the unwrap-failure message names
   `.v0.bak` as the recovery path. Any change moves these deliberately or not at all.

2. **`set_passphrase` is silent about it.** `crypto.py:238-251` rewraps the live key
   under scrypt and logs `vault_passphrase_configured`. It does not look for the adjacent
   plaintext copy, so the operator is told the vault is passphrase-protected while the
   passphrase is bypassable from the same directory. That is R-0011: the protection
   claimed is not the protection delivered, and the gap appears in no output at all.

3. **The window to fix this cheaply is open and closes at merge.** This code is not on
   `main`. `main` and `origin/main` still carry the pre-keywrap 52-line `crypto.py` with
   zero references to `_migrate_plaintext_key`. The 260-line version exists only on three
   unmerged branches - `fix/pooled-key-custody`, `fix/proxy-model-resolution`,
   `fix/r0012-ascii-cli-output` - where the file is byte-identical (md5 `83bcfb888d51`),
   plus a 123-line intermediate on `feat/openfree-token-budget`. No shipped vault has
   been migrated by released code. Fixing before merge costs a branch edit; fixing after
   merge costs a migration against real operator vaults.

`docs/SECRETS_CUSTODY.md` never mentions `.v0.bak`. The document that is the custody
record is silent on the highest-value plaintext artifact on disk.

The repo has already made this exact call once, for a lower-value secret.
`docs/SECRETS_CUSTODY.md:72-74` records that the ship PAT became a sealed KeyVault row
and "legacy `pat.json` is migrated once then deleted". The master key decrypts every
secret including that PAT, and currently gets weaker treatment than the PAT did.

## Considered Options

- **(a) Keep the backup forever, silently.** The status quo. Maximum recoverability, and
  it is what the code and the tests currently assert.
- **(b) Delete the backup at migration time,** immediately after the round-trip
  verification that `_write_key_file` already performs.
- **(c) Verify then retire.** Keep writing the backup, make its presence visible in the
  status the operator actually reads, and add one audited route that removes it after
  proving the wrapped key unwraps to the same bytes.
- **(d) Wrap the backup too.** Rejected on inspection rather than on taste: the backup
  exists precisely for the case where the wrap cannot be unwrapped. A wrapped backup is
  not a recovery path for a broken wrap, it is a second copy of the same failure. This
  option will be proposed again by someone who has not read this paragraph.

## Decision Outcome

Chosen option: **(c) verify then retire**, with the visibility half treated as
non-optional rather than as a nice-to-have that ships later.

Option (b) is rejected for the reason the original docstring gives, which still holds:
`_write_key_file` verifies a wrap round-trips in-process before replacing the file
(`crypto.py:47-51`, asserted by `test_write_verifies_round_trip_before_replacing`), but
an in-process round trip does not prove the key survives a reboot, a Windows profile
change, or a DPAPI edge case that only appears on the next cold start. Deleting on the
strength of a same-process check trades a visible risk for an invisible one.

Option (a) is rejected because it is only defensible if it is written down and surfaced,
and once it is surfaced the operator immediately needs a safe way to act on it - which is
(c).

Implementation of (c) landed on `fix/proxy-model-resolution` with
`OpenMW/tests/test_master_key_backup_custody.py`. This record stays **proposed**
until the founder accepts.

Four sub-decisions follow, matching the four questions raised at intake.

### 1. `set_passphrase` warns loudly. It does not refuse.

Refusing is the instinct and it is wrong here, for two independent reasons.

It refuses legitimate work (R-0005). Setting a passphrase while a backup exists is
strictly better than not setting one: it protects the wrapped key, and the operator who
is refused does not thereby become safer. They either give up and configure no passphrase
at all, or they delete the backup by hand with no verification - which is the exact
unverified shred this record exists to prevent.

It is also a wire change with an in-repo consumer. `POST /api/vault/passphrase`
(`app.py:2499`) currently returns 200 with `Seal.status()`. Turning that into a 400
changes a published route and requires `apps/web` to adopt the new failure in the same
breath, which AGENT_SYSTEM section 4 forbids inside one epic.

The additive shape reaches a human where refusing would not. `Seal.status()`
(`crypto.py:180-186`) gains `plaintext_backup_present: bool`, and it is returned by
`GET /api/vault/status` (`app.py:2475`), by `POST /api/vault/passphrase`, and by
`/api/vault/unseal` and `/api/vault/lock`, all of which already spread `**status()`. That
field lands in `apps/web/src/lib/api/secrets.ts:73-74` and renders in
`apps/web/src/components/vault/SecretsPanel.tsx:286-317` - the panel that already prints
"passphrase configured", and the exact screen on which HT3 is cleared. A structlog line
would not have reached that screen, and a degradation visible only in a log is the
R-0011 failure restated, not fixed.

Two corrections to that path, found by reading it rather than assuming it. The Python half
is genuinely automatic: `/api/vault/status`, `/unseal`, `/lock` and `/passphrase` all
return `{"ok": True, **state_seal.status()}`, so one new field reaches all four with no
route edit. The TypeScript half is not automatic. `VaultStatus`
(`apps/web/src/lib/api/secrets.ts:70-75`) is an explicit interface and must gain the field
by hand, so S3 carries that edit rather than inheriting it.

The second correction is a trap that would have shipped. In `SecretsPanel.tsx` the warning
treatment (`border-warning-border bg-warning-bg`) is gated on `sealed`; the unsealed branch
renders a single muted line in `text-muted-foreground` that appends `wrap=...` and
`passphrase configured` as trailing fragments (`:316-317`). An operator who has just set a
passphrase is unsealed, so appending `plaintext backup present` to that line puts the
warning in grey trailing text, on the exact screen HT3 is cleared, immediately beside the
reassurance it contradicts. That is R-0011 a second time, expressed in CSS rather than in
a log level. **S3 must decouple the warning treatment from `sealed`:** the backup condition
is orthogonal to seal state and must carry warning styling in both branches, or the field
ships technically visible and functionally silent.

**Yes, if:** we refuse instead of warn, if and only if the refusal ships as its own
consumer epic after the status field is published, and the founder accepts that operators
between the two releases can set no passphrase at all.

### 2. The gate criterion goes on HT3, with a cross-reference from HT5.

HT3 is where the wrong conclusion is formed. A human who clears "Passphrase unseal / lock
UX on `:3010`" concludes the vault is passphrase-protected, and a criterion belongs where
the false belief is drawn, not merely where a similar word appears.

HT5's subject is a different artifact at a different moment: secrets travelling to a
remote host at FreeBuild execute, checked in host env and logs. Folding a local-disk
custody check into it dilutes a gate that is already carrying a full leak class. HT5
gains one cross-reference line, not the criterion.

HT3 becomes: passphrase unseal / lock UX on `:3010`, **and** the vault status on that
screen reports no plaintext backup present, **and** the vault sealed refuse holds.

### 3. There is an audited retire route. Retirement is never a hand-run `rm`.

The verify half largely exists; the retire half does not. The new route must:

- require loopback and an unsealed vault, like every sibling custody mutation
  (`app.py:2491-2517`);
- unwrap the live `master.key` through the real `keywrap.unwrap` path and compare the
  result byte-for-byte with the contents of `master.key.v0.bak`;
- refuse when they differ, because a backup that is not a backup of this key is a
  different and more alarming problem than a leftover file;
- only then remove the backup, and write a `_audit_custody` row (`app.py:375`) naming the
  path removed.

It is a new route, `POST /api/vault/backup/retire`, with no existing consumer: contract
impact additive. DR-0009 sets the precedent for the shape - the direction that destroys
value gets its own audited route rather than a flag on a general update, so nothing
reaches it by accident.

### 4. `docs/SECRETS_CUSTODY.md` records the outcome, including the negative case.

The document gains a row in the custody table for `master.key.v0.bak` and a paragraph
stating what it is, what it bypasses, how to see it, and how to retire it. Had option (a)
been chosen, the same paragraph would still be required, saying that a plaintext master
key copy is kept indefinitely by design and why. The unacceptable state is the current
one, where the behaviour exists and no document admits it.

## Consequences

- Good: the assertion in PRD-001 section 4 becomes checkable from the customer's seat.
  An operator can copy a vault folder, see `plaintext_backup_present` on `:3010`, retire
  the backup, and re-test - all without reading the codebase.
- Good: the invariant is asserted at the layer the customer receives it. The custody
  customer receives a directory, and the new field is derived from that directory rather
  than from an in-process flag (R-0001).
- Bad: an operator who retires the backup and then hits a genuine DPAPI failure has lost
  their recovery path. That is the real cost of this record, it is not hypothetical, and
  it is why retirement is opt-in and gated on a byte comparison rather than performed
  automatically at migration.
- Bad: two existing assertions must move deliberately. `test_keywrap.py:142` becomes
  "migration leaves a backup and the status reports it present". `test_keywrap.py:180`
  stays as written - the unwrap-failure message must still name the recovery path - but
  only for as long as a backup exists.
- Neutral: `_migrate_plaintext_key` keeps writing the backup. Nothing about the migration
  becomes less recoverable; only the silence ends.
- Deliberate omission: no automatic retirement on a timer, on a reboot count, or on a
  successful cold-start unwrap. Every one of those destroys key material without a human
  present, and the whole point of this record is that destroying key material is the
  irreversible half.

## Confirmation

`OpenMW/tests/test_master_key_backup_custody.py`.

**This file does not exist yet, and this record must not flip to `accepted` until it
does.** DOCUMENT_SYSTEM section Tier 5 names the estate's own precedent for why: rule
R-0001 cites `tests/invariants/test_envelope.py`, which was never written, and a decision
that names a non-existent enforcer is a wish.

The mutation matrix the file must satisfy, each control failing independently (R-0007):

| mutation | result |
|---|---|
| `Seal.status()` stops reporting `plaintext_backup_present` | status test fails |
| retire route skips the byte comparison against the unwrapped live key | mismatch test fails |
| retire route removes the backup while the vault is sealed | loopback/unsealed test fails |
| retire route removes the backup without a `_audit_custody` row | audit test fails |

One further assertion belongs in that file and has no mutation row because it is the
whole defect: a vault folder containing `keys.db` plus `master.key.v0.bak` and nothing
else must not yield plaintext. Today it does.

## Merge order: any, because the guard is what makes order irrelevant

Resolved 2026-08-20. The founder's answer to the NEEDS-YOU was "any merge order". That is
satisfiable, but only by adding a constraint, not by relaxing one.

`crypto.py` is byte-identical on `fix/pooled-key-custody`, `fix/proxy-model-resolution`
and `fix/r0012-ascii-cli-output` (md5 `83bcfb888d51`), and `main` is 0 commits ahead of
each. The fix cannot simply land on `main` first: `_migrate_plaintext_key` does not exist
there, because `main` still carries the 52-line pre-keywrap file. So the fix necessarily
rides in on a branch, and every branch merging afterwards carries a stale copy of the same
file.

Ordering alone cannot solve that. Whichever branch goes first, two stale copies remain,
and the revert arrives either as a conflict resolved under time pressure or as a squash
whose final diff nobody re-reads. Choosing an order only moves which merge is the
dangerous one. It does not remove the danger, which is why "any" is the right answer and
"pick one carefully" is not.

What makes order irrelevant is a control that fails on the reverted tree:

1. The fix and `OpenMW/tests/test_master_key_backup_custody.py` land in the **same
   commit**, on whichever branch merges first. Any branch, founder's choice, no ordering
   constraint.
2. From that merge onward the test is on `main`. Any later branch carrying the stale
   `crypto.py` produces a merged tree where `main`'s test meets the branch's unfixed
   source, and CI fails on that pull request. The silent revert becomes a loud one.
3. The remaining branches rebase onto `main` after the first merge, so they carry the fix
   and present no `crypto.py` conflict to mis-resolve.

This upgrades the Confirmation section's requirement rather than restating it. That test
file was already blocking `accepted`; it is now also blocking the **first merge**, because
it is the only reason "any merge order" is a safe answer. Landing the fix without it
re-opens the question this section closes.

Verify the guard can fail before trusting it green (R-0007): check out the fixed `main`,
revert `crypto.py` alone to `83bcfb888d51`, and confirm the suite goes red. A guard nobody
has seen fail is not known to be a guard.
