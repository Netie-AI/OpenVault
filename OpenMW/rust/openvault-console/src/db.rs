use std::path::Path;
use std::sync::Mutex;

use rusqlite::Connection;

use crate::error::{AppError, AppResult};

pub struct Db {
    conn: Mutex<Connection>,
}

impl Db {
    pub fn open(path: &Path) -> AppResult<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| AppError::Internal(format!("create db dir: {e}")))?;
        }
        let conn = Connection::open(path)?;
        conn.execute_batch("PRAGMA foreign_keys = ON;")?;
        let db = Self {
            conn: Mutex::new(conn),
        };
        db.migrate()?;
        Ok(db)
    }

    pub fn open_in_memory() -> AppResult<Self> {
        let conn = Connection::open_in_memory()?;
        conn.execute_batch("PRAGMA foreign_keys = ON;")?;
        let db = Self {
            conn: Mutex::new(conn),
        };
        db.migrate()?;
        Ok(db)
    }

    pub fn with_conn<F, T>(&self, f: F) -> AppResult<T>
    where
        F: FnOnce(&Connection) -> AppResult<T>,
    {
        let guard = self
            .conn
            .lock()
            .map_err(|_| AppError::Internal("db lock poisoned".into()))?;
        f(&guard)
    }

    fn migrate(&self) -> AppResult<()> {
        self.with_conn(|conn| {
            conn.execute_batch(
                r#"
                CREATE TABLE IF NOT EXISTS accounts (
                  id TEXT PRIMARY KEY,
                  username TEXT NOT NULL UNIQUE,
                  password_hash TEXT NOT NULL,
                  netie_email TEXT NOT NULL UNIQUE,
                  gmail TEXT,
                  phone TEXT,
                  status TEXT NOT NULL,
                  email_verified INTEGER NOT NULL DEFAULT 0,
                  phone_verified INTEGER NOT NULL DEFAULT 0,
                  passkey_default INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS verify_codes (
                  id TEXT PRIMARY KEY,
                  account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                  channel TEXT NOT NULL,
                  target TEXT NOT NULL,
                  code_hash TEXT NOT NULL,
                  expires_at TEXT NOT NULL,
                  consumed INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS passkeys (
                  id TEXT PRIMARY KEY,
                  account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                  credential_id TEXT NOT NULL UNIQUE,
                  public_key_b64 TEXT NOT NULL,
                  device_label TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS passkey_challenges (
                  id TEXT PRIMARY KEY,
                  username TEXT NOT NULL,
                  challenge_b64 TEXT NOT NULL,
                  purpose TEXT NOT NULL,
                  expires_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS vault_secrets (
                  id TEXT PRIMARY KEY,
                  account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                  label TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  provider TEXT NOT NULL DEFAULT '',
                  role TEXT NOT NULL DEFAULT 'backup',
                  ciphertext_b64 TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  revoked INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS sessions (
                  token TEXT PRIMARY KEY,
                  account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                  auth_method TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  expires_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS openship_plans (
                  id TEXT PRIMARY KEY,
                  account_id TEXT,
                  payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                "#,
            )?;
            Ok(())
        })
    }
}
