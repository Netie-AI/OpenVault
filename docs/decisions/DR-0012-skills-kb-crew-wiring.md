---
status: proposed
date: 2026-08-25
decision-makers: founder
---

# DR-0012 - Skills, Netie KB, and Cortex crew: one loop, three stores

**Proposed, not accepted.** This record is the RFC. It does not add a skills
library to OpenVault. It names where each piece lives so cortex-crew, the
correction agent, outreach, and distillation do not scatter.

## Context and Problem Statement

Agent outreach email is already going out. Reviewers sometimes do not understand
it. The wanted loop is:

1. Capture that review automatically.
2. Distill it into Netie KB.
3. Learn a human-sounding chat/email skill (the way Claude or GPT already writes).
4. Hand that skill to Grok so the **next** outreach uses it this turn, not after
   someone remembers to search.
5. Keep the same registry for **internal** skills: what orchestration is, what
   STATUS says right now, how surfaces wire.
6. Stand up a Cortex crew that fires many agents, talks agent-to-agent, spawns
   follow-on tasks, and still finishes the parent task.

If that corpus is dumped into OpenVault, this repo becomes a second skill store
and a third orchestrator. If it lives only in Netie KB, the next email never
loads it. If it lives only as Cursor skill files, Cortex crew never sees it.

STATUS already flags this as **NEEDS-YOU**: a skill store here is a PRODUCT_ROLES
amendment across four repos, not a ticket. OmniRoute `mcp` / `agent-skills` /
`omni-skills` were skipped as "not the vault/ship story"
(`docs/CLAUDE_DECISIONS.md`).

## Considered Options

- **OpenVault owns the skill library.** Rejected. One key vault, not a second
  skill store.
- **Cortex owns the skill library.** Rejected. Crew packs plus `Cortex/skills`
  plus Cursor packs already forked six names.
- **Netie-KB is the one registry, Cortex stirs, OpenVault gates.** This
  record. Matches NETIE.md R-0016 and §3.

## Decision Outcome

### The one orchestration layer

**Cortex stirs.** Tool calling, graph/loop engineering, skill pick, MCP invoke,
crew spawn, parent-task completion, and the **deficit** (`this node needs skill
X / MCP Y / tool Z`) live only there.

OpenVault does not pick DAG vs LangGraph, does not run the crew, and does not
hold skill bodies. Netie KB does not invoke tools. Netie Control (plane 4)
supervises; Cortex does not grow a UI organ.

Crew inference order, every hop:

```
Cortex graph says NEED
  -> OpenVault resolve / POST /api/crew/gate says WHERE + ALLOWED
    -> Cortex loads the skill from Netie-KB :8030 (R-0016)
    -> or invokes MCP (catalog in KB, credentials in OpenVault)
    -> or sends outreach
```

Grok is the serving model in an OpenVault slot, not a separate app.

### Three stores (do not merge them)

| Store | Holds | Used when | Never holds |
|-------|-------|-----------|-------------|
| **Netie-KB** `:8030` (R-0016) | **The one skill registry** (bodies + when-to-use). MCP tool catalog. Distilled reviews. | This turn (Cursor, Claude, Crew all load here). Cold start. Promote after a review. | Provider keys, leave verdicts, crew parent/child graph |
| **OpenVault** | **The one key vault.** MCP server credentials. Access signposts. Leave/send/connect gate. Model slot preference. | Before anything leaves or spends a key. #39 custody MCP (keys + passwords, deny PAN). | Skill text, system prompts, tool graphs |
| **Cortex** | Parent-run + child-run graph. Architecture preset. Live tool-calling loop. | Stir, spawn, finish the parent task. Watchdog of runs. | Keys, a second skill library, an estate UI |

IDE Cursor skills, Claude skills, and `crew/skill_packs` are **clients** of
Netie-KB. Forks of the same name with different bodies (chat-human 21 vs 47
lines) are the bug R-0009 names. They must generate from the registry, not
edit in five trees.

### What must not merge into Cortex

- **Netie Control** is the supervised estate shell (plane 4). First page:
  live runs, ledger, refusal, estate gate (never a cached green). Buttons
  for Netie apps live there, not inside Cortex.
- **Constructor** stays `D:\Constructor` / `Netie-AI/constructor`. Default
  graph: connector -> ontology -> insight -> foundry -> app. Engine is
  Cortex; frontend can be reached via Netie. Do not merge the app.
- **Cortex-crew** is a git worktree of Cortex. Merging it means merging that
  **branch**, not a sixth product. This cloud agent cannot see `Netie-AI/Cortex`
  (404) so it cannot merge that branch from here.

### Two speeds: immediate vs distilled

Reviews must hit **both**, through one ingest, not two competing writers.

```
outreach sent (Cortex) + leave gate (OpenVault)
  -> recipient review / "I don't understand"
  -> one distill ingest (Netie capture -> kb.py)
  -> KB row (learning corpus for later sessions)
  -> promote survivor in Netie-KB (same registry Cursor/Claude/Crew load)
  -> NEXT outreach agent loads that skill this turn
```

- **Immediate:** Cortex registry. Grok (or whoever is in the slot) is instructed
  by the skill, not by a raw Claude transcript and not by a KB search.
- **Distilled:** Netie KB. New sessions and the correction agent search here
  when the graph has no skill yet.

Do not skip the promote step. A KB-only loop is how the next email stays
robotic. A skill-only loop is how the lesson dies when the crew restarts.

Teacher models (Claude, GPT) write the human example. Distill writes the skill.
The serving model (Grok, or the OpenVault slot) **uses** the skill. OpenVault
still only stores which model is selected, not the skill.

### Internal system skills (same registry, different tag)

Crew agents need the same load path for:

- what orchestration is (this record + PRODUCT_ROLES)
- what is true **right now** (`STATUS.md` per repo, not a second status file)
- how surfaces wire (OpenVault gate, Cortex loop, FreeIDE code, AirGPT shell)

Tag them `system`. Do not copy CLAUDE.md into Netie KB as a second law. Law
stays in each repo. KB stores distilled **lessons**; the registry stores
**loadable** system skills that point at those files.

### Cortex crew: spawn without dropping the parent

When the crew fires many agents:

1. One **parent run** owns the original task until it is done or explicitly
   cancelled. Child chats do not replace it.
2. Children spawn from a graph node that already names the deficit (skill / MCP
   / tool). Do not spawn a child to "go figure out which skill".
3. Agent-to-agent messages are work on the same graph, not a new orchestrator
   in AirGPT or OpenVault.
4. Correction agent reads: parent status, the deficit, the skill that was
   loaded, the review that came back. It does not invent a parallel loop.
5. OpenVault is asked only `may this child invoke / leave / spend`.

### What OpenVault may add later without becoming the library

Compatible with PRODUCT_ROLES, no founder amendment required:

- Access kinds `skill` / `mcp` as **signposts** (id, owner, URL, gate), the
  same way `memory` already points at Cortex and returns no rows.
- MCP credentials in the vault.
- #39 thin client / custody MCP: retrieve keys and site passwords, hard-deny
  PAN, no disk cache.

Still a PRODUCT_ROLES amendment (do not build here):

- A `/api/skills` that returns prompt text, skill bodies, or "when to use".
- Distill ingest, outreach send-loop, crew scheduler.

## Open decisions - these need the founder

1. Accept this wiring (Netie-KB registry + OpenVault keys/gate + Cortex stir)
   and close STATUS "Skills library NEEDS-YOU" as **not OpenVault**.
2. Grant this agent `Netie-AI/Cortex`, `netie-control`, `Netie`, `Netie-KB`
   so Cortex-crew can be merged if gates are green and the estate gate can
   be repaired where Control already shows FAILING.
3. Who runs distill ingest (Netie script vs Cortex job). OpenVault still does
   not.

## Consequences

- Good: next outreach can load a human-email skill this turn; reviews still
  accumulate in KB; crew has one graph for deficit and parent completion;
  OpenVault stays the gate.
- Bad: three places to look, on purpose. The failure mode to watch is a fourth
  place (Cursor-only, AirGPT-only, or an OpenVault catalog).
- Neutral: `/api/access` now signposts `netie-kb.skills` / `netie-kb.mcp` at
  `:8030`. Those entries must never grow skill-body fields.

## Wire Cortex crew must call (OpenVault half, shipped)

This environment cannot write `Netie-AI/Cortex` (private, 404 with the agent
token). The OpenVault side of the conversation is implemented so Cortex agents
have a stable HTTP contract the moment that repo is reachable.

**Netie-KB owns and must serve** (not implemented here; repo 404):

| Path | Returns |
|------|---------|
| REST+MCP on `:8030` | Skill ids + bodies. One registry. Cursor/Claude/Crew clients. |
| MCP catalog | Tool schemas. Credentials stay in OpenVault. |

**Cortex owns and must serve** (not implemented here; repo 404):

| Path | Returns |
|------|---------|
| `GET /api/crew` | Parent-run ids + status. No transcripts. |

**Cortex crew calls OpenVault** (implemented):

```
NEED on graph
  -> POST {openvault}/api/crew/gate
       {kind, id, intent: invoke, parent_run_id, child_id, deficit}
  -> location + allowed. No skill_body.
  -> if allowed: Cortex loads the skill / invokes MCP / sends outreach
  -> if leave/send: same gate with intent leave
```

Connect pack (`GET /api/local/connect-pack`) now publishes:

- `openvault.crew_gate`, `openvault.access_resolve`
- `cortex.crew`
- `netie_kb.skills` / `netie_kb.mcp` at `:8030`
- `constructor` pointer (skin, not merged)

`GET /api/cortex/skills` is an **index of the KB registry** (ids only, bodies
stripped). `GET /api/cortex/crew` indexes Cortex parent-run ids.

## Confirmation

`OpenMW/tests/test_contract.py`:

- `test_no_skills_library_route` — no `/api/skills`, `/api/agent-skills`, or
  `/api/omni-skills` content route.
- `test_product_roles_openvault_does_not_run_the_loop` — PRODUCT_ROLES still
  says running the agent loop is not OpenVault's job.

`OpenMW/tests/test_access_routing.py`:

- `test_access_registry_is_not_a_skill_store` — `skill`/`mcp` kinds exist as
  signposts; policy still forbids running the agent loop; no skill-body fields.
- `test_skill_resolves_to_cortex_and_returns_no_content`
- `test_crew_gate_is_resolve_plus_audit`

`OpenMW/tests/test_cortex_crew_client.py`:

- `test_skills_index_keeps_ids_and_drops_bodies` — a Cortex payload that
  includes `skill_body` is stripped before it leaves OpenVault.
- `test_crew_index_keeps_run_ids_and_drops_transcripts`

Mutation-check (R-0007): adding `/api/skills` (a body catalog) or a
`skill_body` on a registry entry must fail those tests. A decision with no
enforcer is a wish (DR-0001).
