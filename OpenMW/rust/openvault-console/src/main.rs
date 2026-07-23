use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

use axum::Router;
use openvault_console::api::{router as api_router, AppState};
use openvault_console::crypto_util::random_token;
use openvault_console::db::Db;
use tower_http::cors::CorsLayer;
use tower_http::services::{ServeDir, ServeFile};
use tower_http::trace::TraceLayer;
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env().add_directive("info".parse()?))
        .init();

    let home = std::env::var("OPENVAULT_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| dirs_fallback());
    std::fs::create_dir_all(&home)?;
    let db_path = home.join("rust-auth.db");
    let master_path = home.join("vault.master");
    let vault_master = load_or_create_master(&master_path)?;

    let demo_mode = std::env::var("OPENVAULT_DEMO")
        .map(|v| v != "0" && v.to_lowercase() != "false")
        .unwrap_or(true);

    let db = Arc::new(Db::open(&db_path)?);
    let state = AppState {
        db,
        vault_master,
        demo_mode,
    };

    let static_dir = static_dir();
    let index = static_dir.join("index.html");

    let app = Router::new()
        .merge(api_router(state))
        .fallback_service(ServeDir::new(&static_dir).not_found_service(ServeFile::new(index)))
        .layer(CorsLayer::permissive())
        .layer(TraceLayer::new_for_http());

    let host = std::env::var("OPENVAULT_HOST").unwrap_or_else(|_| "127.0.0.1".into());
    let port: u16 = std::env::var("OPENVAULT_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(5055);
    let addr: SocketAddr = format!("{host}:{port}").parse()?;
    tracing::info!(
        %addr,
        demo_mode,
        db = %db_path.display(),
        "openvault rust console listening"
    );
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

fn dirs_fallback() -> PathBuf {
    if let Ok(home) = std::env::var("HOME") {
        return PathBuf::from(home).join(".openvault");
    }
    PathBuf::from(".openvault")
}

fn load_or_create_master(path: &PathBuf) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    if path.exists() {
        return Ok(std::fs::read(path)?);
    }
    let token = random_token();
    std::fs::write(path, token.as_bytes())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = std::fs::metadata(path)?.permissions();
        perms.set_mode(0o600);
        std::fs::set_permissions(path, perms)?;
    }
    Ok(token.into_bytes())
}

fn static_dir() -> PathBuf {
    if let Ok(p) = std::env::var("OPENVAULT_STATIC") {
        return PathBuf::from(p);
    }
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("static");
    if manifest.is_dir() {
        return manifest;
    }
    PathBuf::from("static")
}
