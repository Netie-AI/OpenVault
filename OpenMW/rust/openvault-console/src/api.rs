use std::sync::Arc;

use axum::extract::{Path, State};
use axum::http::HeaderMap;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::catalog::{fallback_status, provider_catalog};
use crate::crypto_util::{generate_passkey_keypair, sign_challenge};
use crate::db::Db;
use crate::error::{AppError, AppResult};
use crate::openship::{build_plan, execute_simulate};

#[derive(Clone)]
pub struct AppState {
    pub db: Arc<Db>,
    pub vault_master: Vec<u8>,
    pub demo_mode: bool,
}

fn bearer(headers: &HeaderMap) -> AppResult<String> {
    let auth = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let token = auth.strip_prefix("Bearer ").unwrap_or("").trim();
    if token.is_empty() {
        return Err(AppError::Unauthorized("Bearer token required".into()));
    }
    Ok(token.to_string())
}

async fn healthz() -> Json<Value> {
    let openvault = std::env::var("OPENVAULT_URL").unwrap_or_else(|_| "http://127.0.0.1:5000".into());
    Json(json!({
        "status": "ok",
        "service": "openvault-console-rust",
        "features": ["auth", "passkeys", "vault", "omniroute", "openship", "mesh"],
        "mesh": {
            "openvault": openvault,
            "connect_pack": format!("{}/api/local/connect-pack", openvault.trim_end_matches('/')),
            "role": "rust_console"
        }
    }))
}

#[derive(Deserialize)]
struct RegisterBody {
    username: String,
    password: String,
}

async fn register(
    State(st): State<AppState>,
    Json(body): Json<RegisterBody>,
) -> AppResult<Json<Value>> {
    let acct = st
        .db
        .register_username_password(&body.username, &body.password)?;
    Ok(Json(json!({
        "account": acct,
        "next": "verify_gmail",
        "message": "Registered. Assigned netie email. Verify with Gmail, then phone, then register a laptop passkey."
    })))
}

#[derive(Deserialize)]
struct GmailStart {
    account_id: String,
    gmail: String,
}

async fn gmail_start(
    State(st): State<AppState>,
    Json(body): Json<GmailStart>,
) -> AppResult<Json<Value>> {
    Ok(Json(st.db.start_gmail_verification(
        &body.account_id,
        &body.gmail,
        st.demo_mode,
    )?))
}

#[derive(Deserialize)]
struct CodeBody {
    account_id: String,
    code: String,
}

async fn gmail_confirm(
    State(st): State<AppState>,
    Json(body): Json<CodeBody>,
) -> AppResult<Json<Value>> {
    let acct = st.db.confirm_gmail(&body.account_id, &body.code)?;
    Ok(Json(json!({ "account": acct, "next": "verify_phone" })))
}

#[derive(Deserialize)]
struct PhoneStart {
    account_id: String,
    phone: String,
}

async fn phone_start(
    State(st): State<AppState>,
    Json(body): Json<PhoneStart>,
) -> AppResult<Json<Value>> {
    Ok(Json(st.db.start_phone_verification(
        &body.account_id,
        &body.phone,
        st.demo_mode,
    )?))
}

async fn phone_confirm(
    State(st): State<AppState>,
    Json(body): Json<CodeBody>,
) -> AppResult<Json<Value>> {
    let acct = st.db.confirm_phone(&body.account_id, &body.code)?;
    Ok(Json(json!({
        "account": acct,
        "next": "register_passkey",
        "message": "Account active. Register a passkey on this laptop — password not needed for daily login."
    })))
}

#[derive(Deserialize)]
struct UsernameBody {
    username: String,
}

async fn passkey_register_begin(
    State(st): State<AppState>,
    Json(body): Json<UsernameBody>,
) -> AppResult<Json<Value>> {
    let mut challenge = st.db.begin_passkey_register(&body.username)?;
    // Demo helper: server can mint a keypair for laptop registration UI tests.
    let (sk, pk) = generate_passkey_keypair();
    let sig = sign_challenge(&sk, challenge["challenge"].as_str().unwrap_or(""))?;
    challenge["demo_private_key"] = json!(sk);
    challenge["demo_public_key"] = json!(pk);
    challenge["demo_signature"] = json!(sig);
    challenge["demo_credential_id"] = json!(format!("cred_{}", uuid::Uuid::new_v4()));
    Ok(Json(challenge))
}

#[derive(Deserialize)]
struct PasskeyRegisterFinish {
    challenge_id: String,
    credential_id: String,
    public_key_b64: String,
    device_label: String,
    signature_b64: String,
}

async fn passkey_register_finish(
    State(st): State<AppState>,
    Json(body): Json<PasskeyRegisterFinish>,
) -> AppResult<Json<Value>> {
    let info = st.db.finish_passkey_register(
        &body.challenge_id,
        &body.credential_id,
        &body.public_key_b64,
        &body.device_label,
        &body.signature_b64,
    )?;
    Ok(Json(json!({
        "passkey": info,
        "message": "Passkey stored. Default login is passkey on this laptop."
    })))
}

async fn passkey_login_begin(
    State(st): State<AppState>,
    Json(body): Json<UsernameBody>,
) -> AppResult<Json<Value>> {
    Ok(Json(st.db.begin_passkey_login(&body.username)?))
}

#[derive(Deserialize)]
struct PasskeyLoginFinish {
    challenge_id: String,
    credential_id: String,
    signature_b64: String,
}

async fn passkey_login_finish(
    State(st): State<AppState>,
    Json(body): Json<PasskeyLoginFinish>,
) -> AppResult<Json<Value>> {
    let (account, token) = st.db.finish_passkey_login(
        &body.challenge_id,
        &body.credential_id,
        &body.signature_b64,
    )?;
    Ok(Json(json!({
        "account": account,
        "token": token,
        "auth_method": "passkey"
    })))
}

#[derive(Deserialize)]
struct PasswordLogin {
    username: String,
    password: String,
}

async fn password_login(
    State(st): State<AppState>,
    Json(body): Json<PasswordLogin>,
) -> AppResult<Json<Value>> {
    let (account, token) = st.db.login_password(&body.username, &body.password)?;
    Ok(Json(json!({
        "account": account,
        "token": token,
        "auth_method": if account.passkey_default { "password_backup" } else { "password" },
        "warning": if account.passkey_default {
            "Passkey is preferred — password kept as argon2id backup only"
        } else {
            "Register a laptop passkey to drop password from daily login"
        }
    })))
}

async fn me(State(st): State<AppState>, headers: HeaderMap) -> AppResult<Json<Value>> {
    let token = bearer(&headers)?;
    let account = st.db.account_from_session(&token)?;
    let passkeys = st.db.list_passkeys(&account.id)?;
    Ok(Json(json!({ "account": account, "passkeys": passkeys })))
}

async fn list_accounts(State(st): State<AppState>) -> AppResult<Json<Value>> {
    Ok(Json(json!({ "accounts": st.db.list_accounts()? })))
}

#[derive(Deserialize)]
struct VaultPut {
    label: String,
    kind: Option<String>,
    provider: Option<String>,
    role: Option<String>,
    secret: String,
}

async fn vault_put(
    State(st): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<VaultPut>,
) -> AppResult<Json<Value>> {
    let token = bearer(&headers)?;
    let account = st.db.account_from_session(&token)?;
    let rec = st.db.vault_put(
        &st.vault_master,
        &account.id,
        &body.label,
        body.kind.as_deref().unwrap_or("api_key"),
        body.provider.as_deref().unwrap_or("custom"),
        body.role.as_deref().unwrap_or("backup"),
        &body.secret,
    )?;
    Ok(Json(json!({ "secret": rec })))
}

async fn vault_list(State(st): State<AppState>, headers: HeaderMap) -> AppResult<Json<Value>> {
    let token = bearer(&headers)?;
    let account = st.db.account_from_session(&token)?;
    Ok(Json(json!({
        "secrets": st.db.vault_list(&st.vault_master, &account.id)?
    })))
}

async fn vault_reveal(
    State(st): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> AppResult<Json<Value>> {
    let token = bearer(&headers)?;
    let _account = st.db.account_from_session(&token)?;
    let secret = st.db.vault_reveal(&st.vault_master, &id)?;
    Ok(Json(json!({ "id": id, "secret": secret })))
}

async fn vault_revoke(
    State(st): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> AppResult<Json<Value>> {
    let token = bearer(&headers)?;
    let _account = st.db.account_from_session(&token)?;
    st.db.vault_revoke(&id)?;
    Ok(Json(json!({ "revoked": true, "id": id })))
}

async fn vault_incident(
    State(st): State<AppState>,
    headers: HeaderMap,
) -> AppResult<Json<Value>> {
    let token = bearer(&headers)?;
    let account = st.db.account_from_session(&token)?;
    let n = st.db.vault_kill_account(&account.id)?;
    Ok(Json(json!({
        "account_id": account.id,
        "revoked": n,
        "message": "All vault secrets killed for this account"
    })))
}

async fn providers() -> Json<Value> {
    Json(json!({
        "providers": provider_catalog(),
        "fallback": fallback_status(),
        "source": "OmniRoute patterns (curated)"
    }))
}

#[derive(Deserialize)]
struct ShipBody {
    project_path: String,
    subdomain: String,
    action: Option<String>,
    execute: Option<bool>,
}

async fn openship_plan(
    State(st): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ShipBody>,
) -> AppResult<Json<Value>> {
    let account_id = match bearer(&headers) {
        Ok(token) => st.db.account_from_session(&token).ok().map(|a| a.id),
        Err(_) => None,
    };
    let mut plan = build_plan(
        &body.project_path,
        &body.subdomain,
        body.action.as_deref().unwrap_or("install"),
    );
    if body.execute.unwrap_or(false) {
        plan = execute_simulate(plan);
    }
    st.db
        .save_ship_plan(account_id.as_deref(), &plan)?;
    Ok(Json(serde_json::to_value(plan).unwrap()))
}

async fn openship_list(State(st): State<AppState>) -> AppResult<Json<Value>> {
    Ok(Json(json!({ "ships": st.db.list_ship_plans()? })))
}

async fn openship_get(
    State(st): State<AppState>,
    Path(id): Path<String>,
) -> AppResult<Json<Value>> {
    let plan = st.db.load_ship_plan(&id)?;
    Ok(Json(serde_json::to_value(plan).unwrap()))
}

/// Client-side helper: sign a challenge with a stored private key (laptop passkey).
#[derive(Deserialize)]
struct SignBody {
    private_key_b64: String,
    challenge: String,
}

async fn demo_sign(Json(body): Json<SignBody>) -> AppResult<Json<Value>> {
    let sig = sign_challenge(&body.private_key_b64, &body.challenge)?;
    Ok(Json(json!({ "signature_b64": sig })))
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/api/healthz", get(healthz))
        .route("/api/auth/register", post(register))
        .route("/api/auth/verify/gmail/start", post(gmail_start))
        .route("/api/auth/verify/gmail/confirm", post(gmail_confirm))
        .route("/api/auth/verify/phone/start", post(phone_start))
        .route("/api/auth/verify/phone/confirm", post(phone_confirm))
        .route("/api/auth/passkey/register/begin", post(passkey_register_begin))
        .route(
            "/api/auth/passkey/register/finish",
            post(passkey_register_finish),
        )
        .route("/api/auth/passkey/login/begin", post(passkey_login_begin))
        .route("/api/auth/passkey/login/finish", post(passkey_login_finish))
        .route("/api/auth/login/password", post(password_login))
        .route("/api/auth/me", get(me))
        .route("/api/auth/demo/sign", post(demo_sign))
        .route("/api/accounts", get(list_accounts))
        .route("/api/vault/secrets", get(vault_list).post(vault_put))
        .route("/api/vault/secrets/:id/reveal", get(vault_reveal))
        .route("/api/vault/secrets/:id/revoke", post(vault_revoke))
        .route("/api/vault/incident", post(vault_incident))
        .route("/api/providers", get(providers))
        .route("/api/openship/plan", post(openship_plan))
        .route("/api/openship", get(openship_list))
        .route("/api/openship/:id", get(openship_get))
        .with_state(state)
}
