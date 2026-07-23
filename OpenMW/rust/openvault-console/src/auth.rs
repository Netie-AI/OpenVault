use chrono::{Duration, Utc};
use rusqlite::params;
use serde::Serialize;
use uuid::Uuid;

use crate::crypto_util::{
    hash_code, hash_password, random_challenge, random_digits, random_token, verify_password,
    verify_passkey_signature,
};
use crate::db::Db;
use crate::error::{AppError, AppResult};

#[derive(Debug, Clone, Serialize)]
pub struct AccountPublic {
    pub id: String,
    pub username: String,
    pub netie_email: String,
    pub gmail: Option<String>,
    pub phone: Option<String>,
    pub status: String,
    pub email_verified: bool,
    pub phone_verified: bool,
    pub passkey_default: bool,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct PasskeyInfo {
    pub id: String,
    pub credential_id: String,
    pub device_label: String,
    pub created_at: String,
}

fn now_str() -> String {
    Utc::now().to_rfc3339()
}

fn clean_username(raw: &str) -> AppResult<String> {
    let u = raw.trim().to_lowercase();
    if u.len() < 3 || u.len() > 32 {
        return Err(AppError::BadRequest(
            "username must be 3–32 characters".into(),
        ));
    }
    if !u
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '.' || c == '-')
    {
        return Err(AppError::BadRequest(
            "username may only contain a-z 0-9 _ . -".into(),
        ));
    }
    Ok(u)
}

impl Db {
    pub fn register_username_password(
        &self,
        username: &str,
        password: &str,
    ) -> AppResult<AccountPublic> {
        let username = clean_username(username)?;
        if password.len() < 8 {
            return Err(AppError::BadRequest(
                "password must be at least 8 characters (stored argon2id only)".into(),
            ));
        }
        let id = Uuid::new_v4().to_string();
        let netie_email = format!("{username}@netie.ai");
        let password_hash = hash_password(password)?;
        let now = now_str();
        self.with_conn(|conn| {
            let exists: bool = conn.query_row(
                "SELECT EXISTS(SELECT 1 FROM accounts WHERE username = ?1)",
                params![username],
                |r| r.get(0),
            )?;
            if exists {
                return Err(AppError::Conflict("username already taken".into()));
            }
            conn.execute(
                r#"
                INSERT INTO accounts (
                  id, username, password_hash, netie_email, gmail, phone, status,
                  email_verified, phone_verified, passkey_default, created_at, updated_at
                ) VALUES (?1,?2,?3,?4,NULL,NULL,'pending_email',0,0,0,?5,?5)
                "#,
                params![id, username, password_hash, netie_email, now],
            )?;
            Ok(())
        })?;
        self.get_account_by_id(&id)?
            .ok_or_else(|| AppError::Internal("account missing after insert".into()))
    }

    pub fn get_account_by_id(&self, id: &str) -> AppResult<Option<AccountPublic>> {
        self.with_conn(|conn| {
            let mut stmt = conn.prepare(
                r#"
                SELECT id, username, netie_email, gmail, phone, status,
                       email_verified, phone_verified, passkey_default, created_at, updated_at
                FROM accounts WHERE id = ?1
                "#,
            )?;
            let mut rows = stmt.query(params![id])?;
            if let Some(row) = rows.next()? {
                Ok(Some(row_to_account(row)?))
            } else {
                Ok(None)
            }
        })
    }

    pub fn get_account_by_username(&self, username: &str) -> AppResult<Option<AccountPublic>> {
        let username = username.trim().to_lowercase();
        self.with_conn(|conn| {
            let mut stmt = conn.prepare(
                r#"
                SELECT id, username, netie_email, gmail, phone, status,
                       email_verified, phone_verified, passkey_default, created_at, updated_at
                FROM accounts WHERE username = ?1
                "#,
            )?;
            let mut rows = stmt.query(params![username])?;
            if let Some(row) = rows.next()? {
                Ok(Some(row_to_account(row)?))
            } else {
                Ok(None)
            }
        })
    }

    pub fn list_accounts(&self) -> AppResult<Vec<AccountPublic>> {
        self.with_conn(|conn| {
            let mut stmt = conn.prepare(
                r#"
                SELECT id, username, netie_email, gmail, phone, status,
                       email_verified, phone_verified, passkey_default, created_at, updated_at
                FROM accounts ORDER BY created_at DESC
                "#,
            )?;
            let rows = stmt.query_map([], |row| Ok(row_to_account(row).unwrap()))?;
            let mut out = Vec::new();
            for r in rows {
                out.push(r?);
            }
            Ok(out)
        })
    }

    /// Gmail used for verification only — Netie email remains username@netie.ai.
    pub fn start_gmail_verification(
        &self,
        account_id: &str,
        gmail: &str,
        demo_mode: bool,
    ) -> AppResult<serde_json::Value> {
        let gmail = gmail.trim().to_lowercase();
        if !gmail.ends_with("@gmail.com") && !gmail.ends_with("@googlemail.com") {
            return Err(AppError::BadRequest(
                "verification email must be a Gmail address".into(),
            ));
        }
        let account = self
            .get_account_by_id(account_id)?
            .ok_or_else(|| AppError::NotFound("account not found".into()))?;
        if account.email_verified {
            return Err(AppError::BadRequest("gmail already verified".into()));
        }
        let code = random_digits(6);
        let code_hash = hash_code(&code);
        let id = Uuid::new_v4().to_string();
        let expires = (Utc::now() + Duration::minutes(15)).to_rfc3339();
        let now = now_str();
        self.with_conn(|conn| {
            conn.execute(
                "UPDATE accounts SET gmail = ?1, updated_at = ?2 WHERE id = ?3",
                params![gmail, now, account_id],
            )?;
            conn.execute(
                r#"
                INSERT INTO verify_codes (id, account_id, channel, target, code_hash, expires_at, consumed)
                VALUES (?1,?2,'gmail',?3,?4,?5,0)
                "#,
                params![id, account_id, gmail, code_hash, expires],
            )?;
            Ok(())
        })?;
        let mut v = serde_json::json!({
            "account_id": account_id,
            "channel": "gmail",
            "target": gmail,
            "expires_at": expires,
            "message": "Enter the code sent to Gmail (demo returns code locally)."
        });
        if demo_mode {
            v["demo_code"] = serde_json::json!(code);
        }
        Ok(v)
    }

    pub fn confirm_gmail(&self, account_id: &str, code: &str) -> AppResult<AccountPublic> {
        self.consume_code(account_id, "gmail", code)?;
        let now = now_str();
        self.with_conn(|conn| {
            conn.execute(
                r#"
                UPDATE accounts
                SET email_verified = 1, status = 'pending_phone', updated_at = ?1
                WHERE id = ?2
                "#,
                params![now, account_id],
            )?;
            Ok(())
        })?;
        self.get_account_by_id(account_id)?
            .ok_or_else(|| AppError::NotFound("account not found".into()))
    }

    pub fn start_phone_verification(
        &self,
        account_id: &str,
        phone: &str,
        demo_mode: bool,
    ) -> AppResult<serde_json::Value> {
        let phone = phone.trim().to_string();
        if phone.len() < 8 {
            return Err(AppError::BadRequest("phone number too short".into()));
        }
        let account = self
            .get_account_by_id(account_id)?
            .ok_or_else(|| AppError::NotFound("account not found".into()))?;
        if !account.email_verified {
            return Err(AppError::BadRequest(
                "verify Gmail before phone verification".into(),
            ));
        }
        if account.phone_verified {
            return Err(AppError::BadRequest("phone already verified".into()));
        }
        let code = random_digits(6);
        let code_hash = hash_code(&code);
        let id = Uuid::new_v4().to_string();
        let expires = (Utc::now() + Duration::minutes(15)).to_rfc3339();
        let now = now_str();
        self.with_conn(|conn| {
            conn.execute(
                "UPDATE accounts SET phone = ?1, updated_at = ?2 WHERE id = ?3",
                params![phone, now, account_id],
            )?;
            conn.execute(
                r#"
                INSERT INTO verify_codes (id, account_id, channel, target, code_hash, expires_at, consumed)
                VALUES (?1,?2,'phone',?3,?4,?5,0)
                "#,
                params![id, account_id, phone, code_hash, expires],
            )?;
            Ok(())
        })?;
        let mut v = serde_json::json!({
            "account_id": account_id,
            "channel": "phone",
            "target": phone,
            "expires_at": expires,
            "message": "Enter the SMS code (demo returns code locally)."
        });
        if demo_mode {
            v["demo_code"] = serde_json::json!(code);
        }
        Ok(v)
    }

    pub fn confirm_phone(&self, account_id: &str, code: &str) -> AppResult<AccountPublic> {
        self.consume_code(account_id, "phone", code)?;
        let now = now_str();
        self.with_conn(|conn| {
            conn.execute(
                r#"
                UPDATE accounts
                SET phone_verified = 1, status = 'active', updated_at = ?1
                WHERE id = ?2
                "#,
                params![now, account_id],
            )?;
            Ok(())
        })?;
        self.get_account_by_id(account_id)?
            .ok_or_else(|| AppError::NotFound("account not found".into()))
    }

    fn consume_code(&self, account_id: &str, channel: &str, code: &str) -> AppResult<()> {
        let code_hash = hash_code(code.trim());
        let now = Utc::now().to_rfc3339();
        self.with_conn(|conn| {
            let row = conn.query_row(
                r#"
                SELECT id, expires_at FROM verify_codes
                WHERE account_id = ?1 AND channel = ?2 AND code_hash = ?3 AND consumed = 0
                ORDER BY expires_at DESC LIMIT 1
                "#,
                params![account_id, channel, code_hash],
                |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)),
            );
            let (id, expires_at) = match row {
                Ok(v) => v,
                Err(rusqlite::Error::QueryReturnedNoRows) => {
                    return Err(AppError::Unauthorized("invalid verification code".into()));
                }
                Err(e) => return Err(AppError::Db(e)),
            };
            if expires_at < now {
                return Err(AppError::Unauthorized("verification code expired".into()));
            }
            conn.execute(
                "UPDATE verify_codes SET consumed = 1 WHERE id = ?1",
                params![id],
            )?;
            Ok(())
        })
    }

    pub fn begin_passkey_register(
        &self,
        username: &str,
    ) -> AppResult<serde_json::Value> {
        let account = self
            .get_account_by_username(username)?
            .ok_or_else(|| AppError::NotFound("account not found".into()))?;
        if account.status != "active" {
            return Err(AppError::BadRequest(
                "complete Gmail + phone verification before registering a passkey".into(),
            ));
        }
        let challenge = random_challenge();
        let id = Uuid::new_v4().to_string();
        let expires = (Utc::now() + Duration::minutes(5)).to_rfc3339();
        self.with_conn(|conn| {
            conn.execute(
                r#"
                INSERT INTO passkey_challenges (id, username, challenge_b64, purpose, expires_at)
                VALUES (?1,?2,?3,'register',?4)
                "#,
                params![id, account.username, challenge, expires],
            )?;
            Ok(())
        })?;
        Ok(serde_json::json!({
            "challenge_id": id,
            "challenge": challenge,
            "username": account.username,
            "rp_id": "openvault.local",
            "user_id": account.id,
        }))
    }

    pub fn finish_passkey_register(
        &self,
        challenge_id: &str,
        credential_id: &str,
        public_key_b64: &str,
        device_label: &str,
        signature_b64: &str,
    ) -> AppResult<PasskeyInfo> {
        let (username, challenge) = self.take_challenge(challenge_id, "register")?;
        if !verify_passkey_signature(public_key_b64, &challenge, signature_b64)? {
            return Err(AppError::Unauthorized("passkey signature invalid".into()));
        }
        let account = self
            .get_account_by_username(&username)?
            .ok_or_else(|| AppError::NotFound("account not found".into()))?;
        let id = Uuid::new_v4().to_string();
        let now = now_str();
        self.with_conn(|conn| {
            conn.execute(
                r#"
                INSERT INTO passkeys (id, account_id, credential_id, public_key_b64, device_label, created_at)
                VALUES (?1,?2,?3,?4,?5,?6)
                "#,
                params![
                    id,
                    account.id,
                    credential_id,
                    public_key_b64,
                    device_label,
                    now
                ],
            )?;
            conn.execute(
                "UPDATE accounts SET passkey_default = 1, updated_at = ?1 WHERE id = ?2",
                params![now, account.id],
            )?;
            Ok(())
        })?;
        Ok(PasskeyInfo {
            id,
            credential_id: credential_id.to_string(),
            device_label: device_label.to_string(),
            created_at: now,
        })
    }

    pub fn begin_passkey_login(&self, username: &str) -> AppResult<serde_json::Value> {
        let account = self
            .get_account_by_username(username)?
            .ok_or_else(|| AppError::NotFound("account not found".into()))?;
        if !account.passkey_default {
            return Err(AppError::BadRequest(
                "no passkey registered — complete laptop registration first".into(),
            ));
        }
        let challenge = random_challenge();
        let id = Uuid::new_v4().to_string();
        let expires = (Utc::now() + Duration::minutes(5)).to_rfc3339();
        let creds = self.list_passkeys(&account.id)?;
        self.with_conn(|conn| {
            conn.execute(
                r#"
                INSERT INTO passkey_challenges (id, username, challenge_b64, purpose, expires_at)
                VALUES (?1,?2,?3,'login',?4)
                "#,
                params![id, account.username, challenge, expires],
            )?;
            Ok(())
        })?;
        Ok(serde_json::json!({
            "challenge_id": id,
            "challenge": challenge,
            "username": account.username,
            "allow_credentials": creds.iter().map(|c| &c.credential_id).collect::<Vec<_>>(),
        }))
    }

    pub fn finish_passkey_login(
        &self,
        challenge_id: &str,
        credential_id: &str,
        signature_b64: &str,
    ) -> AppResult<(AccountPublic, String)> {
        let (username, challenge) = self.take_challenge(challenge_id, "login")?;
        let account = self
            .get_account_by_username(&username)?
            .ok_or_else(|| AppError::NotFound("account not found".into()))?;
        let pk = self.with_conn(|conn| {
            conn.query_row(
                "SELECT public_key_b64 FROM passkeys WHERE account_id = ?1 AND credential_id = ?2",
                params![account.id, credential_id],
                |r| r.get::<_, String>(0),
            )
            .map_err(|_| AppError::Unauthorized("unknown passkey credential".into()))
        })?;
        if !verify_passkey_signature(&pk, &challenge, signature_b64)? {
            return Err(AppError::Unauthorized("passkey signature invalid".into()));
        }
        let token = self.create_session(&account.id, "passkey")?;
        Ok((account, token))
    }

    /// Password login is backup only — discouraged once passkeys exist.
    pub fn login_password(
        &self,
        username: &str,
        password: &str,
    ) -> AppResult<(AccountPublic, String)> {
        let username = username.trim().to_lowercase();
        let (id, hash, passkey_default) = self.with_conn(|conn| {
            conn.query_row(
                "SELECT id, password_hash, passkey_default FROM accounts WHERE username = ?1",
                params![username],
                |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, i64>(2)?)),
            )
            .map_err(|_| AppError::Unauthorized("invalid username or password".into()))
        })?;
        if !verify_password(password, &hash)? {
            return Err(AppError::Unauthorized(
                "invalid username or password".into(),
            ));
        }
        let account = self
            .get_account_by_id(&id)?
            .ok_or_else(|| AppError::NotFound("account not found".into()))?;
        let method = if passkey_default == 1 {
            "password_backup"
        } else {
            "password"
        };
        let token = self.create_session(&account.id, method)?;
        Ok((account, token))
    }

    pub fn list_passkeys(&self, account_id: &str) -> AppResult<Vec<PasskeyInfo>> {
        self.with_conn(|conn| {
            let mut stmt = conn.prepare(
                r#"
                SELECT id, credential_id, device_label, created_at
                FROM passkeys WHERE account_id = ?1 ORDER BY created_at DESC
                "#,
            )?;
            let rows = stmt.query_map(params![account_id], |row| {
                Ok(PasskeyInfo {
                    id: row.get(0)?,
                    credential_id: row.get(1)?,
                    device_label: row.get(2)?,
                    created_at: row.get(3)?,
                })
            })?;
            let mut out = Vec::new();
            for r in rows {
                out.push(r?);
            }
            Ok(out)
        })
    }

    pub fn create_session(&self, account_id: &str, auth_method: &str) -> AppResult<String> {
        let token = random_token();
        let now = now_str();
        let expires = (Utc::now() + Duration::hours(12)).to_rfc3339();
        self.with_conn(|conn| {
            conn.execute(
                r#"
                INSERT INTO sessions (token, account_id, auth_method, created_at, expires_at)
                VALUES (?1,?2,?3,?4,?5)
                "#,
                params![token, account_id, auth_method, now, expires],
            )?;
            Ok(())
        })?;
        Ok(token)
    }

    pub fn account_from_session(&self, token: &str) -> AppResult<AccountPublic> {
        let now = Utc::now().to_rfc3339();
        let account_id: String = self.with_conn(|conn| {
            conn.query_row(
                r#"
                SELECT account_id FROM sessions
                WHERE token = ?1 AND expires_at >= ?2
                "#,
                params![token, now],
                |r| r.get(0),
            )
            .map_err(|_| AppError::Unauthorized("session expired or missing".into()))
        })?;
        self.get_account_by_id(&account_id)?
            .ok_or_else(|| AppError::Unauthorized("session account missing".into()))
    }

    fn take_challenge(&self, challenge_id: &str, purpose: &str) -> AppResult<(String, String)> {
        let now = Utc::now().to_rfc3339();
        self.with_conn(|conn| {
            let row = conn.query_row(
                r#"
                SELECT username, challenge_b64, expires_at FROM passkey_challenges
                WHERE id = ?1 AND purpose = ?2
                "#,
                params![challenge_id, purpose],
                |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, String>(2)?)),
            );
            let (username, challenge, expires_at) = match row {
                Ok(v) => v,
                Err(rusqlite::Error::QueryReturnedNoRows) => {
                    return Err(AppError::Unauthorized("challenge not found".into()));
                }
                Err(e) => return Err(AppError::Db(e)),
            };
            conn.execute(
                "DELETE FROM passkey_challenges WHERE id = ?1",
                params![challenge_id],
            )?;
            if expires_at < now {
                return Err(AppError::Unauthorized("challenge expired".into()));
            }
            Ok((username, challenge))
        })
    }
}

fn row_to_account(row: &rusqlite::Row<'_>) -> rusqlite::Result<AccountPublic> {
    Ok(AccountPublic {
        id: row.get(0)?,
        username: row.get(1)?,
        netie_email: row.get(2)?,
        gmail: row.get(3)?,
        phone: row.get(4)?,
        status: row.get(5)?,
        email_verified: row.get::<_, i64>(6)? != 0,
        phone_verified: row.get::<_, i64>(7)? != 0,
        passkey_default: row.get::<_, i64>(8)? != 0,
        created_at: row.get(9)?,
        updated_at: row.get(10)?,
    })
}
