//! OmniRoute-style provider catalog + fallback roles (patterns, not a 250-provider fork).

use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct ProviderSpec {
    pub id: &'static str,
    pub name: &'static str,
    pub tier: &'static str,
    pub default_role: &'static str,
    pub register_url: &'static str,
    pub free_notes: &'static str,
    pub needed_by: &'static [&'static str],
}

pub fn provider_catalog() -> Vec<ProviderSpec> {
    vec![
        ProviderSpec {
            id: "ollama",
            name: "Ollama",
            tier: "local",
            default_role: "free",
            register_url: "https://ollama.com",
            free_notes: "Local free forever",
            needed_by: &["openvault", "cortex", "airgpt"],
        },
        ProviderSpec {
            id: "cortex",
            name: "Cortex / Netie Engine",
            tier: "local",
            default_role: "primary",
            register_url: "https://github.com/Netie-AI/Cortex",
            free_notes: "Local engine",
            needed_by: &["openvault", "cortex"],
        },
        ProviderSpec {
            id: "openrouter",
            name: "OpenRouter",
            tier: "freemium",
            default_role: "backup",
            register_url: "https://openrouter.ai",
            free_notes: "Free model hops",
            needed_by: &["cortex", "airgpt"],
        },
        ProviderSpec {
            id: "groq",
            name: "Groq",
            tier: "freemium",
            default_role: "cheap",
            register_url: "https://console.groq.com",
            free_notes: "Fast free tier",
            needed_by: &["cortex", "airgpt"],
        },
        ProviderSpec {
            id: "google",
            name: "Google AI Studio",
            tier: "freemium",
            default_role: "backup",
            register_url: "https://aistudio.google.com/apikey",
            free_notes: "Gemini free tier",
            needed_by: &["airgpt"],
        },
        ProviderSpec {
            id: "openai",
            name: "OpenAI",
            tier: "paid",
            default_role: "primary",
            register_url: "https://platform.openai.com/api-keys",
            free_notes: "Paid",
            needed_by: &["cortex", "airgpt"],
        },
        ProviderSpec {
            id: "anthropic",
            name: "Anthropic",
            tier: "paid",
            default_role: "primary",
            register_url: "https://console.anthropic.com",
            free_notes: "Paid",
            needed_by: &["cortex", "airgpt"],
        },
        ProviderSpec {
            id: "github_models",
            name: "GitHub Models",
            tier: "freemium",
            default_role: "free",
            register_url: "https://github.com/marketplace/models",
            free_notes: "GitHub free models",
            needed_by: &["airgpt"],
        },
    ]
}

#[derive(Debug, Clone, Serialize)]
pub struct FallbackStatus {
    pub role_order: Vec<&'static str>,
    pub note: &'static str,
}

pub fn fallback_status() -> FallbackStatus {
    FallbackStatus {
        role_order: vec!["primary", "backup", "cheap", "free"],
        note: "OmniRoute-style 4-tier fallback; vault secrets supply hops",
    }
}
