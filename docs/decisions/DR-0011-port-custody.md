---
status: proposed
date: 2026-08-20
decision-makers: founder
---

# DR-0011 - Port custody: name the blocker, and let the operator keep a port

**Proposed, not accepted.** The implementation below is built and verified, but four
questions in "Open decisions" are the founder's to settle. This record is the RFC.

## Context and Problem Statement

Three failures this week were the same missing answer, and the last one cost an afternoon:

1. Every launcher had an "already listening on :5000 - reusing it" branch. It reused
   *whatever* was there. The desktop shell adopted a server started by a PowerShell script
   that pointed at a different vault home, and the console painted and then failed.
2. When a port was busy the message was "port busy". An operator cannot act on that. They
   need the application's name and executable path so they know what to close.
3. Changing a port meant editing a module constant in source, so nobody ever did.

## Decision Outcome

`openmw/openvault/ports.py` owns port resolution and port ownership.

**Naming the blocker.** `describe_listener(port)` returns the pid, process name and
executable path of whatever holds a port, via `psutil` - already a dependency, so no new
one was added. Verified on Windows 11 **without elevation**: all 54 listening sockets
resolved to a name and path, including system processes. Where the OS will not attribute a
listener, the result says "an application this account cannot inspect" rather than
reporting the port free - an honest unknown beats a confident wrong answer.

**Keeping a port.** `openmw ports --set api=5099` writes `$OPENVAULT_HOME/ports.json`.
Resolution precedence is explicit flag > environment variable > saved file > default, so a
one-off `--port` never rewrites a saved preference.

**Refusing narrowly.** A listener is only "foreign" if it fails to identify itself as ours
on its health endpoint. Our own already-running server is still reused, because a control
that refuses legitimate work is a failure, not a win (R-0005). External services on their
own ports - Cortex :8010, AirGPT :8765 - are never treated as intruders.

**Never killing.** Naming a process is decision support for the operator, not a licence to
terminate somebody else's work. See open decision 3.

## Open decisions - these need the founder

### 1. Where does `ports.json` live? (blocking correctness)

It currently follows `OPENVAULT_HOME`. In a plain shell that resolves to
`~/.openvault`, while every launcher pins `<repo>/.openvault`. So `openmw ports --set`
run by hand writes one file and `openvault up` reads another.

This is the same unresolved two-vault-home question that produced the Electron finding on
2026-08-19, now with a second symptom. Options: (a) leave it following `OPENVAULT_HOME` and
accept that running the command outside a launcher needs the variable set, (b) pin port
settings to one fixed per-device path independent of the vault home, (c) fix the root cause
and make `paths.openvault_home()` default to `<repo>/.openvault` - which moves where
`keys.db` is looked for, and is therefore not an agent's call.

### 2. The web port cannot actually move yet

`apps/web/package.json` hardcodes `--port 3010` in its `dev` and `start` scripts, and an
explicit Next `--port` beats `$PORT`. `ports.py` records this in `fixed_reason` and says so
rather than pretending, but the setting is inert for `web` until those scripts read the
environment. Small change; it touches the web build, so it is flagged rather than done.

### 3. Should anything ever be killed?

The request said "after kill that process". Nothing is killed today. A stale OpenVault
process the operator genuinely wants gone is a real case, and telling them to go find it in
Task Manager is weak. The narrow version - an explicit `openvault ports --kill api` that
names the application and asks for confirmation, refusing outright when the process is not
ours - is implementable, but terminating another application's process is not something an
agent should decide to add.

### 4. Cortex and AirGPT are reported, never reconfigured

PRODUCT_ROLES puts them in other repos. `set_port` refuses them with a typed error naming
the reason. If the founder wants OpenVault to own mesh-wide port allocation, that is a
PRODUCT_ROLES amendment across repos, not a ticket here.

## Consequences

- Good: `openvault up` refuses in about 10 seconds naming the blocking application, instead
  of adopting a stranger's server or waiting 90 seconds and reporting a timeout.
- Good: the duplication between the launcher's small resolver and the package's is a gated
  invariant - `tests/test_ports.py` fails if they disagree - rather than a drift risk. The
  launcher runs on the system Python and cannot import the OpenMW package, so some
  duplication is structural.
- Bad: one more file under `OPENVAULT_HOME`, and open decision 1 means it can be written to
  a home the launcher will not read. Visible rather than silent: every `ports` run prints
  the file path it used.
- Neutral: `_port_busy()` remains in the launcher for the cheap "is anything there" check.
  Only the decision that follows it changed.

## Confirmation

`OpenMW/tests/test_ports.py`, asserting on real bound sockets rather than a mocked psutil -
a test that mocks the process lookup would pass on a machine where the lookup does not work.

Verified live end to end, not just in unit tests: with `api=5099` saved, `openmw console`
bound :5099 and nothing answered on :5000.

Mutation-checked (R-0007):

| mutation | result |
|---|---|
| launcher resolver ignores the saved file | 4 tests fail (drift gate) |
| `ports` command stops exiting non-zero when blocked | exit-code contract tests fail |

Two errors caught during implementation are worth recording, because both looked correct:

- A command-line heuristic was added to recognise our own process cheaply. Every process
  launched from `OpenMW/.venv` has "openmw" in its argv, so it would have adopted a
  stranger's python script as ours - the exact bug this module exists to prevent. Removed;
  only a health response counts as evidence now.
- The first version resolved nothing. `openmw ports --set` wrote the file, while
  `openmw console --port` still defaulted to a hardcoded 5000 and the launcher still had
  `API_PORT = 5000`. The command printed "used on every later start" while nothing read it,
  which is precisely the silent lie R-0011 exists to forbid. Found by an adversarial sweep,
  not by the tests, which is why the live end-to-end check above is now part of this record.
