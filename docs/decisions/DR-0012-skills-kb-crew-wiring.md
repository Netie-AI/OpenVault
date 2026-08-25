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

- **OpenVault owns the skill library** (catalog + bodies + "when to use").
  Rejected unless the founder amends PRODUCT_ROLES. OpenVault's job is custody
  and gate, not the agent loop.
- **Netie KB is the only store.** Rejected as the runtime path. KB is a search
  corpus for new sessions. A live graph node cannot block on `kb.py search`.
- **Cursor `.cursor/skills` only.** Rejected as the crew path. IDE skills are
  one client of the registry, not the registry.
- **Three stores, one stirrer (this record).** Cortex runs the loop and loads
  the skill this turn. OpenVault answers where + may you. Netie KB keeps the
  distilled lessons. Promote survivors from KB into the Cortex registry.

## Decision Outcome

### The one orchestration layer

**Cortex stirs.** Tool calling, graph/loop engineering, skill pick, MCP invoke,
crew spawn, parent-task completion, and the **deficit** (`this node needs skill
X / MCP Y / tool Z`) live only there.

OpenVault does not pick DAG vs LangGraph, does not run the crew, and does not
hold skill bodies. Netie KB does not invoke tools.

Crew inference order, every hop:

```
Cortex graph says NEED
  -> OpenVault resolve says WHERE + ALLOWED
    -> Cortex loads skill / invokes MCP / sends outreach
      -> Netie KB only if the graph has no skill and a new session must recall
```

### Three stores (do not merge them)

| Store | Holds | Used when | Never holds |
|-------|-------|-----------|-------------|
| **Cortex skill registry** | Skill bodies tagged `outreach` or `system`. When-to-use. Live MCP tool schemas the loop will call. Parent-run + child-run graph. | **This turn.** Next outreach. Crew member spawn. Correction agent. | Provider keys, leave-machine verdicts, the review corpus |
| **OpenVault** | MCP server URLs and credentials. Access signposts (same shape as `cortex.memory`: location + gate, never content). Leave/send/connect gate. Model **slot** preference. | Before anything leaves or spends a key. Ticket #39 custody MCP (keys + site passwords, deny PAN). | Skill text, system prompts, tool graphs, distilled reviews |
| **Netie KB** (`kb.py search`, distill ingest) | Reviews of emails people did not understand. Distilled "how Claude/GPT wrote it human". Invariants and lessons for agents who were not in the loop. | Cold start, correction after a miss, promoting a survivor into the Cortex registry | Runtime invoke, live deficit, gate verdicts |

IDE Cursor skills and MCP configs are **clients** of the Cortex registry (and of
OpenVault for secrets). They are not a fourth store.

### Two speeds: immediate vs distilled

Reviews must hit **both**, through one ingest, not two competing writers.

```
outreach sent (Cortex) + leave gate (OpenVault)
  -> recipient review / "I don't understand"
  -> one distill ingest (Netie capture -> kb.py)
  -> KB row (learning corpus for later sessions)
  -> promote survivor -> Cortex skill `outreach.human-email`
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

1. Accept this wiring (Cortex registry + OpenVault signpost + Netie KB corpus)
   as the skills answer, and close the STATUS "Skills library NEEDS-YOU" as
   **not OpenVault**. Work then routes to the PRD agent for Cortex + Netie-KB.
2. Or amend PRODUCT_ROLES so OpenVault owns the skill library. That is four
   repos, not a ticket in this one.
3. Who runs distill ingest (Netie script vs Cortex job). OpenVault still does
   not.

## Consequences

- Good: next outreach can load a human-email skill this turn; reviews still
  accumulate in KB; crew has one graph for deficit and parent completion;
  OpenVault stays the gate.
- Bad: three places to look, on purpose. The failure mode to watch is a fourth
  place (Cursor-only, AirGPT-only, or an OpenVault catalog).
- Neutral: `/api/access` does not grow `skill`/`mcp` kinds in this RFC. When it
  does, those entries must be derived from live Cortex/mesh state, never a
  hardcoded catalogue of bodies.

## Confirmation

`OpenMW/tests/test_contract.py`:

- `test_no_skills_library_route` — no `/api/skills`, `/api/agent-skills`, or
  `/api/omni-skills` content route.
- `test_product_roles_openvault_does_not_run_the_loop` — PRODUCT_ROLES still
  says running the agent loop is not OpenVault's job.

`OpenMW/tests/test_access_routing.py`:

- `test_access_registry_is_not_a_skill_store` — registry kinds stay location
  kinds; policy still forbids running the agent loop; no skill-body fields.

Mutation-check (R-0007): adding `/api/skills` or a `skill_body` on a registry
entry must fail those tests. A decision with no enforcer is a wish (DR-0001).
