"""Host adapters — the seam between "we built it" and "it is on the internet".

The product shape this serves: OpenVault owns no servers. The user's machine
does the build, and the result is pushed into an account the *user* controls,
on a domain the *user* bought. Every adapter here therefore needs exactly two
things from the user — a credential (which lives in the vault) and an account
identifier — and nothing from us.

The one rule every adapter must obey: **never report success you did not
observe.** ``ship/engine.py`` historically emitted a ``simulated`` host step
that read like a deploy, which is why a "Deploy" button could appear to work
while nothing had left the machine. An adapter that cannot deploy must return
``ok=False`` with a reason a non-engineer can act on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class Preflight:
    """Can this adapter run at all, before we spend time building?"""

    ready: bool
    #: What the user must do, phrased as an instruction, when not ready.
    blocker: str = ""
    #: Free-form facts worth showing (account name, detected CLI version).
    facts: dict[str, str] = field(default_factory=dict)


@dataclass
class DeployResult:
    ok: bool
    #: The URL the user can actually open. Empty when ok is False.
    url: str = ""
    #: Provider-side identifier, for a follow-up status call.
    deployment_ref: str = ""
    #: What happened, in the user's words. Always populated.
    detail: str = ""
    #: Raw provider log, for the terminal pane. Never rendered as the summary.
    log: str = ""


@dataclass
class DomainResult:
    ok: bool
    hostname: str = ""
    detail: str = ""
    #: DNS records the user must create when we cannot create them ourselves.
    required_records: list[dict[str, str]] = field(default_factory=list)


@runtime_checkable
class HostAdapter(Protocol):
    """One place a built artifact can be published to."""

    #: Stable id used in the target picker and stored on the deployment.
    id: str
    #: Shown in the UI.
    name: str
    #: Which vault provider holds this adapter's credential.
    credential_provider: str

    def preflight(self) -> Preflight:
        """Check credentials and tooling without mutating anything."""
        ...

    def deploy(self, artifact_dir: Path, *, project: str) -> DeployResult:
        """Publish an already-built directory. Must not build."""
        ...

    def attach_domain(self, *, project: str, hostname: str) -> DomainResult:
        """Point a user-owned hostname at the deployment."""
        ...
