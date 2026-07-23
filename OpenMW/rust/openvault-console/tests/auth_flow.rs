use std::sync::Arc;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use openvault_console::api::{router, AppState};
use openvault_console::db::Db;
use serde_json::{json, Value};
use tower::ServiceExt;

fn state() -> AppState {
    AppState {
        db: Arc::new(Db::open_in_memory().expect("db")),
        vault_master: b"test-master-key-32bytes-minimum!!".to_vec(),
        demo_mode: true,
    }
}

async fn json_req(app: axum::Router, method: &str, uri: &str, body: Value) -> (StatusCode, Value) {
    let req = Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap();
    let res = app.oneshot(req).await.unwrap();
    let status = res.status();
    let bytes = res.into_body().collect().await.unwrap().to_bytes();
    let v: Value = serde_json::from_slice(&bytes).unwrap_or(json!({}));
    (status, v)
}

async fn json_auth(
    app: axum::Router,
    method: &str,
    uri: &str,
    token: &str,
    body: Option<Value>,
) -> (StatusCode, Value) {
    let mut builder = Request::builder()
        .method(method)
        .uri(uri)
        .header("authorization", format!("Bearer {token}"));
    let req = if let Some(b) = body {
        builder = builder.header("content-type", "application/json");
        builder.body(Body::from(b.to_string())).unwrap()
    } else {
        builder.body(Body::empty()).unwrap()
    };
    let res = app.oneshot(req).await.unwrap();
    let status = res.status();
    let bytes = res.into_body().collect().await.unwrap().to_bytes();
    let v: Value = serde_json::from_slice(&bytes).unwrap_or(json!({}));
    (status, v)
}

#[tokio::test]
async fn full_register_verify_passkey_vault_openship() {
    let st = state();
    let app = router(st.clone());

    let (s, reg) = json_req(
        app.clone(),
        "POST",
        "/api/auth/register",
        json!({"username": "acmeops", "password": "supersecret1"}),
    )
    .await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(reg["account"]["netie_email"], "acmeops@netie.ai");
    let account_id = reg["account"]["id"].as_str().unwrap().to_string();

    let (s, gstart) = json_req(
        app.clone(),
        "POST",
        "/api/auth/verify/gmail/start",
        json!({"account_id": account_id, "gmail": "acme@gmail.com"}),
    )
    .await;
    assert_eq!(s, StatusCode::OK);
    let gcode = gstart["demo_code"].as_str().unwrap();

    let (s, _) = json_req(
        app.clone(),
        "POST",
        "/api/auth/verify/gmail/confirm",
        json!({"account_id": account_id, "code": gcode}),
    )
    .await;
    assert_eq!(s, StatusCode::OK);

    let (s, pstart) = json_req(
        app.clone(),
        "POST",
        "/api/auth/verify/phone/start",
        json!({"account_id": account_id, "phone": "+15551234567"}),
    )
    .await;
    assert_eq!(s, StatusCode::OK);
    let pcode = pstart["demo_code"].as_str().unwrap();

    let (s, active) = json_req(
        app.clone(),
        "POST",
        "/api/auth/verify/phone/confirm",
        json!({"account_id": account_id, "code": pcode}),
    )
    .await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(active["account"]["status"], "active");

    let (s, begin) = json_req(
        app.clone(),
        "POST",
        "/api/auth/passkey/register/begin",
        json!({"username": "acmeops"}),
    )
    .await;
    assert_eq!(s, StatusCode::OK);

    let (s, _) = json_req(
        app.clone(),
        "POST",
        "/api/auth/passkey/register/finish",
        json!({
            "challenge_id": begin["challenge_id"],
            "credential_id": begin["demo_credential_id"],
            "public_key_b64": begin["demo_public_key"],
            "device_label": "test-laptop",
            "signature_b64": begin["demo_signature"]
        }),
    )
    .await;
    assert_eq!(s, StatusCode::OK);

    let (s, lbegin) = json_req(
        app.clone(),
        "POST",
        "/api/auth/passkey/login/begin",
        json!({"username": "acmeops"}),
    )
    .await;
    assert_eq!(s, StatusCode::OK);

    let (s, signed) = json_req(
        app.clone(),
        "POST",
        "/api/auth/demo/sign",
        json!({
            "private_key_b64": begin["demo_private_key"],
            "challenge": lbegin["challenge"]
        }),
    )
    .await;
    assert_eq!(s, StatusCode::OK);

    let (s, login) = json_req(
        app.clone(),
        "POST",
        "/api/auth/passkey/login/finish",
        json!({
            "challenge_id": lbegin["challenge_id"],
            "credential_id": begin["demo_credential_id"],
            "signature_b64": signed["signature_b64"]
        }),
    )
    .await;
    assert_eq!(s, StatusCode::OK);
    let token = login["token"].as_str().unwrap();

    let (s, vault) = json_auth(
        app.clone(),
        "POST",
        "/api/vault/secrets",
        token,
        Some(json!({
            "label": "openai",
            "provider": "openai",
            "role": "primary",
            "secret": "sk-test-secret-value"
        })),
    )
    .await;
    assert_eq!(s, StatusCode::OK);
    assert!(vault["secret"]["masked"].as_str().unwrap().contains('…'));

    let (s, ship) = json_req(
        app,
        "POST",
        "/api/openship/plan",
        json!({
            "project_path": "/tmp/demo-app",
            "subdomain": "app.example.com",
            "execute": true
        }),
    )
    .await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(ship["executed"], true);
}

#[tokio::test]
async fn providers_and_health() {
    let app = router(state());
    let req = Request::builder()
        .uri("/api/healthz")
        .body(Body::empty())
        .unwrap();
    let res = app.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::OK);

    let req = Request::builder()
        .uri("/api/providers")
        .body(Body::empty())
        .unwrap();
    let res = app.oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::OK);
    let bytes = res.into_body().collect().await.unwrap().to_bytes();
    let v: Value = serde_json::from_slice(&bytes).unwrap();
    assert!(v["providers"].as_array().unwrap().len() >= 5);
}
