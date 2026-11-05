<!-- Modified by Netie AI, 2026: renamed the patch file for patch-package (npm) and updated this note; Bun is no longer part of the FreeBuild toolchain. -->

`oblien+2.4.0.patch` carries cancellation through the SDK's log requests and
streams, and cancels the response body when a stream finishes. Without it,
closing a Cloud log view leaves idle upstream requests open indefinitely.

The adapter uses the SDK's existing HTTP transport and SSE parser. The version
is pinned until an SDK release includes this fix. `npm install` applies the
patch via the root `postinstall` script.

Tooling note: `patch-package` 8.0.1 fails to apply this exact patch (a generic
"Failed to apply patch" with no useful detail), even though `patch -p1
--dry-run` inside `node_modules/oblien` confirms every hunk applies cleanly
against the published `oblien@2.4.0` tarball — a `tsup`/`tsc` build of
@repo/adapters fails without it (`packages/adapters/src/runtime/cloud.ts`
calls the SDK's log methods with the extra `signal`/`options` parameter this
patch adds). `postinstall` therefore calls the system `patch` command directly
(`patch -p1 -N -s -d node_modules/oblien < patches/oblien+2.4.0.patch`, `-N`
skips a hunk already applied, `|| true` keeps a fresh clone's first install
from failing before node_modules exists) instead of going through
patch-package — Netie's "or equivalent" per the fork brief.
