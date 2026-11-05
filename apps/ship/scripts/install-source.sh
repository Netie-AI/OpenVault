#!/bin/sh
# Modified by Netie AI, 2026: hard-disabled. The upstream installer downloaded
# the upstream project's CLI and release assets. FreeBuild ships no standalone
# installer or CLI; it runs under OpenVault. See apps/ship/README.md and
# docs/decisions/DR-0018-fork-router-and-ship.md.
echo "error: this installer is disabled in FreeBuild. FreeBuild runs under OpenVault; see apps/ship/README.md." >&2
exit 1
