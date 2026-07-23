use chrono::Utc;
use rusqlite::params;
use serde::Serialize;
use uuid::Uuid;

use crate::crypto_util::{seal_secret, unseal_secret};
use crate::db::Db;
use crate::error::{AppError, AppResult};

#[derive(Debug, Clone, Serialize)]
pub struct VaultSecretPublic {
    pub id: String,
    pub account_id: String,
    pub label: String,
    pub kind: String,
    pub provider: String,
    pub role: String,
    pub masked: String,
    pub revoked: bool,
    pub created_at: String,
    pub updated_at: String,
}

fn mask(secret: &str) -> String {
    if secret.len() <= 8 {
        return "••••".into();
    }
    format!("{}…{}", &secret[..4], &secret[secret.len() - 4..])
}

impl Db {
    pub fn vault_put(
        &self,
        master: &[u8],
        account_id: &str,
        label: &str,
        kind: &str,
        provider: &str,
        role: &str,
        secret: &str,
    ) -> AppResult<VaultSecretPublic> {
        let id = Uuid::new_v4().to_string();
        let now = Utc::now().to_rfc3339();
        let ct = seal_secret(master, secret);
        self.with_conn(|conn| {
            conn.execute(
                r#"
                INSERT INTO vault_secrets (
                  id, account_id, label, kind, provider, role, ciphertext_b64, created_at, updated_at, revoked
                ) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?8,0)
                "#,
                params![id, account_id, label, kind, provider, role, ct, now],
            )?;
            Ok(())
        })?;
        Ok(VaultSecretPublic {
            id,
            account_id: account_id.to_string(),
            label: label.to_string(),
            kind: kind.to_string(),
            provider: provider.to_string(),
            role: role.to_string(),
            masked: mask(secret),
            revoked: false,
            created_at: now.clone(),
            updated_at: now,
        })
    }

    pub fn vault_list(&self, master: &[u8], account_id: &str) -> AppResult<Vec<VaultSecretPublic>> {
        self.with_conn(|conn| {
            let mut stmt = conn.prepare(
                r#"
                SELECT id, account_id, label, kind, provider, role, ciphertext_b64, created_at, updated_at, revoked
                FROM vault_secrets WHERE account_id = ?1 ORDER BY created_at DESC
                "#,
            )?;
            let rows = stmt.query_map(params![account_id], |row| {
                let ct: String = row.get(6)?;
                let plain = unseal_secret(master, &ct).unwrap_or_else(|_| "????????".into());
                Ok(VaultSecretPublic {
                    id: row.get(0)?,
                    account_id: row.get(1)?,
                    label: row.get(2)?,
                    kind: row.get(3)?,
                    provider: row.get(4)?,
                    role: row.get(5)?,
                    masked: mask(&plain),
                    created_at: row.get(7)?,
                    updated_at: row.get(8)?,
                    revoked: row.get::<_, i64>(9)? != 0,
                })
            })?;
            let mut out = Vec::new();
            for r in rows {
                out.push(r?);
            }
            Ok(out)
        })
    }

    pub fn vault_reveal(&self, master: &[u8], secret_id: &str) -> AppResult<String> {
        self.with_conn(|conn| {
            let (ct, revoked): (String, i64) = conn
                .query_row(
                    "SELECT ciphertext_b64, revoked FROM vault_secrets WHERE id = ?1",
                    params![secret_id],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .map_err(|_| AppError::NotFound("secret not found".into()))?;
            if revoked != 0 {
                return Err(AppError::BadRequest("secret revoked".into()));
            }
            unseal_secret(master, &ct)
        })
    }

    pub fn vault_revoke(&self, secret_id: &str) -> AppResult<()> {
        let now = Utc::now().to_rfc3339();
        let n = self.with_conn(|conn| {
            Ok(conn.execute(
                "UPDATE vault_secrets SET revoked = 1, updated_at = ?1 WHERE id = ?2",
                params![now, secret_id],
            )?)
        })?;
        if n == 0 {
            return Err(AppError::NotFound("secret not found".into()));
        }
        Ok(())
    }

    pub fn vault_kill_account(&self, account_id: &str) -> AppResult<usize> {
        let now = Utc::now().to_rfc3339();
        self.with_conn(|conn| {
            Ok(conn.execute(
                "UPDATE vault_secrets SET revoked = 1, updated_at = ?1 WHERE account_id = ?2 AND revoked = 0",
                params![now, account_id],
            )?)
        })
    }
}
