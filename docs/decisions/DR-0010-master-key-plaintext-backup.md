---
status: proposed
date: 2026-08-25
decision-makers: founder
---

# DR-0010 - Verify-then-retire `master.key.v0.bak`

**Proposed, not accepted.** Option (c) is implemented. Founder accepts the record.

## Context and Problem Statement

`_migrate_plaintext_key` writes `master.key.v0.bak` (raw unwrapped Fernet master
key) and leaves it on disk. `keywrap.is_wrapped` is an `OVK1` magic check only,
so renaming that bak over `master.key` in a copied vault folder opens every
sealed row with no passphrase and no DPAPI. That falsifies PRD-001 copy
protection (F17).

## Considered Options

- **(a) Stop writing the bak.** Loses the only recovery path if wrap fails after
  migrate.
- **(b) Delete the bak at migrate.** Same recovery loss, plus a race if wrap
  verification is wrong.
- **(c) Keep writing the bak, surface it, retire after verify.** Status reports
  `plaintext_backup_present`. An audited retire route unwraps the live wrap via
  `keywrap.unwrap`, byte-compares to the bak, and deletes only on match.
- **(d) Encrypt the bak under a second wrap.** A second key to lose.

## Decision Outcome

Chosen option: **(c) verify-then-retire**, already chosen in the ticket text for
OpenVault#37.

The bak stays at migrate. `Seal.status()` (and therefore `GET /api/vault/status`)
includes `plaintext_backup_present` whether or not the vault is sealed.
`POST /api/vault/backup/retire` is loopback + unsealed. A vault folder of
`keys.db` + bak and no live wrapped key must not yield plaintext: load never
reads the bak as `master.key`.

`set_passphrase` warns when the bak is present and does not refuse.

This record stays `proposed` until the founder accepts.

## Consequences

- Good: the hole is visible and has an audited close path.
- Good: migrate still has a recovery file if wrap later fails to unwrap.
- Bad: until retire, a copy of the bak next to `keys.db` remains a copy-open
  risk if someone renames it over `master.key`. Status + UI warning exist so
  dumps wait until `plaintext_backup_present` is false.
- Neutral: passphrase-wrapped retire needs the passphrase so `keywrap.unwrap`
  can run on the file, not only the in-memory copy.

## Confirmation

`OpenMW/tests/test_master_key_backup_custody.py`. Mutation matrix:

| mutation | result |
|---|---|
| status omits `plaintext_backup_present` | status tests fail |
| retire skips the byte-compare | mismatch test fails |
| load promotes bak to `master.key` | copy-open test fails |
