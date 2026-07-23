//! OpenShip-style deploy plan + simulate executor (in-repo clone surface).

use chrono::Utc;
use rusqlite::params;
use serde::{Deserialize, Serialize};
use serde_json::json;
use uuid::Uuid;

use crate::db::Db;
use crate::error::{AppError, AppResult};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ShipStep {
    pub id: String,
    pub title: String,
    pub status: String,
    pub detail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ShipPlan {
    pub ship_id: String,
    pub project_path: String,
    pub subdomain: String,
    pub action: String,
    pub steps: Vec<ShipStep>,
    pub ready: bool,
    pub executed: bool,
    pub created_at: String,
}

pub fn build_plan(project_path: &str, subdomain: &str, action: &str) -> ShipPlan {
    let ship_id = Uuid::new_v4().to_string()[..12].to_string();
    let created_at = Utc::now().to_rfc3339();
    let steps = vec![
        ShipStep {
            id: "detect".into(),
            title: "Detect app + services".into(),
            status: if project_path.trim().is_empty() {
                "fail".into()
            } else {
                "pass".into()
            },
            detail: format!("path={project_path}"),
        },
        ShipStep {
            id: "subdomain".into(),
            title: "Subdomain routing".into(),
            status: if subdomain.contains('.') {
                "pass".into()
            } else {
                "fail".into()
            },
            detail: format!("host={subdomain}"),
        },
        ShipStep {
            id: "tls".into(),
            title: "TLS / Let's Encrypt".into(),
            status: if subdomain.contains('.') {
                "pass".into()
            } else {
                "fail".into()
            },
            detail: "ACME plan ready".into(),
        },
        ShipStep {
            id: "mail".into(),
            title: "Secure email DNS".into(),
            status: "pending".into(),
            detail: "SPF/DKIM/DMARC checklist".into(),
        },
        ShipStep {
            id: "build".into(),
            title: "Build / rebuild".into(),
            status: "pass".into(),
            detail: "auto build from detect signals".into(),
        },
        ShipStep {
            id: "apps_services".into(),
            title: format!("Apps + services {action}"),
            status: "pass".into(),
            detail: "scale-only install/update".into(),
        },
        ShipStep {
            id: "playwright".into(),
            title: "Playwright smoke".into(),
            status: "pending".into(),
            detail: "run after roll".into(),
        },
        ShipStep {
            id: "roll".into(),
            title: "Roll / rollback".into(),
            status: "pass".into(),
            detail: "previous release retained".into(),
        },
    ];
    // Hard fails block readiness; mail/playwright may stay pending until live checks.
    let ready = !steps.iter().any(|s| s.status == "fail");
    ShipPlan {
        ship_id,
        project_path: project_path.to_string(),
        subdomain: subdomain.to_string(),
        action: action.to_string(),
        steps,
        ready,
        executed: false,
        created_at,
    }
}

pub fn execute_simulate(mut plan: ShipPlan) -> ShipPlan {
    for step in &mut plan.steps {
        if step.status != "fail" {
            step.status = "simulated".into();
            step.detail = format!("simulated: {}", step.detail);
        }
    }
    plan.executed = true;
    plan.ready = plan.steps.iter().all(|s| s.status != "fail");
    plan
}

impl Db {
    pub fn save_ship_plan(&self, account_id: Option<&str>, plan: &ShipPlan) -> AppResult<()> {
        let payload = serde_json::to_string(plan)
            .map_err(|e| AppError::Internal(format!("serialize ship plan: {e}")))?;
        self.with_conn(|conn| {
            conn.execute(
                r#"
                INSERT INTO openship_plans (id, account_id, payload_json, created_at)
                VALUES (?1,?2,?3,?4)
                "#,
                params![plan.ship_id, account_id, payload, plan.created_at],
            )?;
            Ok(())
        })
    }

    pub fn load_ship_plan(&self, ship_id: &str) -> AppResult<ShipPlan> {
        let payload: String = self.with_conn(|conn| {
            conn.query_row(
                "SELECT payload_json FROM openship_plans WHERE id = ?1",
                params![ship_id],
                |r| r.get(0),
            )
            .map_err(|_| AppError::NotFound("openship plan not found".into()))
        })?;
        serde_json::from_str(&payload)
            .map_err(|e| AppError::Internal(format!("bad ship plan json: {e}")))
    }

    pub fn list_ship_plans(&self) -> AppResult<Vec<serde_json::Value>> {
        self.with_conn(|conn| {
            let mut stmt = conn.prepare(
                "SELECT id, account_id, payload_json, created_at FROM openship_plans ORDER BY created_at DESC LIMIT 50",
            )?;
            let rows = stmt.query_map([], |row| {
                let payload: String = row.get(2)?;
                let mut v: serde_json::Value =
                    serde_json::from_str(&payload).unwrap_or_else(|_| json!({}));
                if let Some(obj) = v.as_object_mut() {
                    obj.insert("account_id".into(), json!(row.get::<_, Option<String>>(1)?));
                }
                Ok(v)
            })?;
            let mut out = Vec::new();
            for r in rows {
                out.push(r?);
            }
            Ok(out)
        })
    }
}
