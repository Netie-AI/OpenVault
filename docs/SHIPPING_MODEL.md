# The shipping model — Vercel-shaped, but you own no servers

## The idea

Vercel and Render are expensive because they rent you their compute. We don't
have compute to rent, and buying it would make this a business with a monthly
burn before the first user arrives.

So invert it:

```
user's machine                     user's cloud account          user's domain
──────────────                     ────────────────────          ─────────────
clone → detect stack → build   →   upload artifact           →   attach hostname
(their CPU, free)                  (their free tier / their bill)
```

**We never touch a server.** Their machine builds. Their cloud account hosts.
Their domain points at it. OpenVault is the control plane that makes those
three steps one button, and the vault is what holds the credentials that let
it happen.

This is a real advantage, not just a cost dodge:

- No egress bill, no build-minute bill, no scaling cliff on our side.
- Nothing to breach centrally — credentials never leave the user's machine.
- The user keeps their cloud account, their domain and their data if they stop
  using us. That is worth saying out loud in the product.

The eventual ad space fits here too: the target picker is the natural surface,
because a user choosing where to host is exactly the moment a hosting provider
wants to be seen. That ranking must stay honest — a paid placement labelled as
a paid placement, never a recommendation dressed as detection.

## Why Cloudflare Pages is the first real target

| | Cloudflare Pages | AWS ECS |
|---|---|---|
| Cost to the user | free tier, genuinely | ECR + Fargate + ALB, billed hourly |
| What we must build | upload a directory | image build, registry push, task def, service, ALB, target group, security groups, ACM cert |
| Custom domain | first-class API object | Route 53 + ACM + listener rules |
| Works with a domain bought elsewhere | yes, via CNAME | yes, via CNAME |

ECS remains the right answer for a container workload with real traffic, and
the adapter interface exists so it can be added without touching the engine.
But shipping ECS first would mean a user's first deploy costs money and takes
twenty resources to provision. Pages means their first deploy is free and takes
one token.

## What is actually implemented

`ship/hosts/` — the adapter seam. `base.py` defines `preflight()`,
`deploy(artifact_dir)` and `attach_domain()`. Adding a target is a module plus
one line in `ADAPTERS`; the engine does not change.

`ship/hosts/cloudflare_pages.py` — real. Verifies the token against
`/user/tokens/verify`, creates the Pages project idempotently, uploads via
`wrangler pages deploy`, and attaches a custom domain through the Pages
domains API.

Two decisions worth knowing:

- **We shell out to wrangler for the upload.** Direct Upload's asset protocol
  (hash negotiation, JWT-scoped upload, manifest commit) is involved and
  versioned; wrangler is Cloudflare's reference implementation. Using it is the
  supported path, not a shortcut. `preflight()` refuses early and tells the
  user to `npm install -g wrangler` rather than discovering it after a build.
- **The deployment URL is parsed from wrangler's output, never constructed.**
  Building `https://{project}.pages.dev` ourselves would produce a link that
  404s while the UI claims the site is live. A zero exit code with no URL in
  the output is treated as a **failure**.

## The rule every adapter follows

**Never report success you did not observe.**

The engine's old `host` step emitted `simulated` for every target, which read
like a deploy in the UI. That is why a Deploy button could appear to work while
nothing had left the machine. Cases that now fail loudly, each with a test:

- artifact directory missing, or present but empty
- no credential, or a token Cloudflare rejects
- wrangler absent
- wrangler exits non-zero (the provider log is surfaced, not swallowed)
- wrangler exits zero but prints no URL
- `run_build=false` — there is no artifact, so there is nothing to publish

## Domains bought elsewhere

A domain on Cloudflare gets its DNS written automatically. A domain bought at
Spaceship, Namecheap or anywhere else cannot be — we have no credential for
that registrar. In that case `attach_domain` returns `required_records`: the
exact CNAME to paste, with the target. Showing the record beats failing
silently, and beats pretending we wired something we did not.

## Next

1. Surface `preflight()` in the UI *before* the build, so a missing token costs
   a second rather than a five-minute build.
2. Auto-pick the target: a static/SSG stack with no server needs → Pages, with
   the reason shown. The user overrides; they do not choose from a blank list.
3. The upload route (`POST /api/ship/library/upload-session` creates a staging
   directory but nothing can send files to it) — needed for drag-a-folder.
4. ECS adapter, behind the same interface, for container workloads.
5. Bill visualisation: real numbers from the provider's billing API, not the
   operator-typed `spent_usd_estimate` that exists today.
