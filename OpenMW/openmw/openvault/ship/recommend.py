"""Recommend a ship target from detected stack — one default, reason shown."""

from __future__ import annotations

from typing import Any

from openmw.openvault.ship.cloud_targets import ShipTarget

# Stacks that ship as static artifacts → Cloudflare Pages (free, one token).
_STATIC_PRIMARY = frozenset(
    {
        "nextjs",
        "next",
        "vite",
        "astro",
        "hugo",
        "jekyll",
        "eleventy",
        "gatsby",
        "remix",
        "nuxt",
        "sveltekit",
        "create-react-app",
        "static",
        "html",
    }
)
_STATIC_CATEGORY = frozenset({"static", "ssg", "spa", "frontend", "jamstack"})
# Needs a long-running process → prefer FreeBuild / VPS (not pretend-Pages).
_SERVER_PRIMARY = frozenset(
    {
        "fastapi",
        "flask",
        "django",
        "express",
        "nestjs",
        "rails",
        "laravel",
        "spring",
        "go",
        "rust-axum",
    }
)
_SERVER_CATEGORY = frozenset({"backend", "api", "fullstack", "server", "container"})


def recommend_target(
    stack: dict[str, Any] | None = None,
    *,
    sponsored_ids: frozenset[str] | set[str] | None = None,
    vps_configured: bool = False,
) -> dict[str, Any]:
    """Pick one target with a human reason. User may override; blank picker is wrong.

    Rule (CLAUDE_DECISIONS §8.5): auto-select **never** picks a sponsored target.

    ``vps_configured`` says the user already has a box we can reach. For a stack
    that needs a running process that changes the honest answer: their VPS is a
    target we can really publish to, where FreeBuild Cloud needs credentials
    they may not have.
    """
    stack = stack or {}
    blocked = frozenset(sponsored_ids or ())
    primary = str(stack.get("primary") or stack.get("framework") or "").strip().lower()
    category = str(stack.get("category") or "").strip().lower()
    output_dir = str(stack.get("output_directory") or "").strip()

    if primary in _SERVER_PRIMARY or category in _SERVER_CATEGORY:
        if vps_configured:
            target: ShipTarget = "vps_ssh"
            reason = (
                f"Detected {primary or category or 'server'} stack — needs a running "
                "process, and you already have a VPS connected. OpenVault builds it "
                "there, runs replicas behind Caddy and gets the TLS certificate."
            )
            real_publish = True
        else:
            target = "openship_cloud"
            reason = (
                f"Detected {primary or category or 'server'} stack — needs a running "
                "process. FreeBuild Cloud (or your own VPS) hosts containers; "
                "Cloudflare Pages is for static folders only."
            )
            real_publish = False
    elif primary in _STATIC_PRIMARY or category in _STATIC_CATEGORY or output_dir:
        target = "cloudflare_pages"
        reason = (
            "Static/SSG (or known output folder) → Cloudflare Pages: free tier, "
            "one API token, custom domain as a first-class API object. Your machine "
            "builds; their account hosts."
        )
        real_publish = True
    else:
        target = "cloudflare_pages"
        reason = (
            "Defaulting to Cloudflare Pages for a free first deploy. Override to "
            "FreeBuild/VPS if this app needs a server process."
        )
        real_publish = True

    # Hard line: if organic pick is sponsored, fall back to local_demo rather than
    # preselecting a paid placement ("we chose this for you" must never be an ad).
    if target in blocked:
        return {
            "ok": True,
            "target": "local_demo",
            "reason": (
                f"Organic pick was {target}, which is marked sponsored — auto-select "
                "never preselects a paid placement. Choose a target yourself."
            ),
            "real_publish": False,
            "sponsored_blocked": True,
            "organic_would_have_been": target,
            "primary": primary or None,
            "category": category or None,
            "output_directory": output_dir or None,
        }

    return {
        "ok": True,
        "target": target,
        "reason": reason,
        "real_publish": real_publish,
        "sponsored_blocked": False,
        "primary": primary or None,
        "category": category or None,
        "output_directory": output_dir or None,
    }
