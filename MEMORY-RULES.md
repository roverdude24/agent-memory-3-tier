# Agent Memory Rules: 3-Tier Memory

The memory contract shared by Pi, OMP, Claude Code, Codex, Hermes, and Gemini/Antigravity. Each harness instruction file (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`) carries the short copy in [AGENTS-snippet.md](AGENTS-snippet.md) and points here.

## The three tiers

| Tier | System | Holds | Written by | Read when |
|---|---|---|---|---|
| 1 · fast | [Lore](https://github.com/roverdude24/lore) (`~/.lore/lore.db`) | Every session transcript and tool call from every harness | Automation only | You need exact past wording, commands, or "what happened in session X" |
| 2 · middle | [Hindsight](https://github.com/vectorize-io/hindsight) (`http://127.0.0.1:8888`) | Durable facts, preferences, and corrections extracted from sessions | Automatic session retain, plus explicit `retain` / `learn` | At session start (recall) and before guessing |
| 3 · truth | [Tolaria](https://tolaria.md) vault (`~/Documents/Tolaria`) | Locked decisions, client facts, approved conventions, project status | Agents, explicitly, at milestones or on request | At session start: the project note |

Flow: sessions → Lore → Hindsight (extraction) → Tolaria (promotion). Nothing writes to Tolaria automatically.

## Precedence

User instruction > repository `PROJECT-STATE.md` > Tolaria > Hindsight > Lore.

When a lower tier contradicts a higher one, act on the higher tier. Fix the lower one when practical: retain a correction in Hindsight, or mark superseded Tolaria notes with `status: Superseded` and `superseded_by:` instead of deleting them.

## Per-harness wiring

| Harness | Lore write | Hindsight bank and retain | Tolaria read |
|---|---|---|---|
| Pi | `pi-memory-bridge.ts` after every settled turn | `pi-memory`; the bridge retains at compaction and shutdown | The bridge injects `project/project-<slug>.md` when relevant |
| OMP | `memory-sync` job (source `omp`) | `omp`; native auto-retain; MCP `hindsight` uses the same bank | MCP `tolaria` at session start |
| Claude Code | `memory-sync` job (source `claude-code`) | `hermes-memory`; `memory-sync` retains sessions idle 30 min | MCP `tolaria` at session start |
| Codex | `memory-sync` job (source `codex`) | `hermes-memory`; `memory-sync` retains sessions idle 30 min | MCP `tolaria` at session start |
| Hermes | `memory-sync` job (source `hermes`) | `hermes-memory`; the Hindsight memory provider retains every turn | MCP `tolaria` at session start |
| Antigravity | `memory-sync` job (source `antigravity`) | MCP only (`hermes-memory`) | MCP `tolaria` at session start |

Banks stay separate per harness. Lore and Tolaria are shared by every harness. Bank names are conventions: rename them, but keep each harness's MCP bank equal to the bank it retains into.

## Session start

1. Read the Tolaria project note `project/project-<slug>.md`. Use `search_notes` when the slug is unclear.
2. Recall from Hindsight for the task. Pi, OMP, and Hermes do this automatically. In Claude Code and Codex, call `recall`.
3. Search Lore only when exact history matters.

## Writing

- **Lore:** never write by hand.
- **Hindsight:** retain corrections, preferences, decisions, and reusable fixes. Never retain secrets, tokens, progress chatter, or guesses.
- **Tolaria:** write only stable truth: locked decisions, client facts, and status at milestones. Use `update_note` or `append_to_note`, preserve frontmatter, and set `updated_at`.

## Promotion (Hindsight → Tolaria)

Promote a fact when the user confirms it, or when it has held across 3+ sessions and is still true. Add it to the project note with its date. Hindsight keeps its copy; Tolaria becomes the authority.

## Lore CLI

Call the Node binary by absolute path: `~/.npm-global/bin/lore search "<query>"`, `lore get <id> --full`, `lore context <id>`. Search is hybrid (keyword + multilingual embeddings) by default; add `--mode keyword` for exact terms. A bare `lore` may resolve to the unrelated Python Lore SDK, which has no `search` command.

MCP equivalents: `search_memory`, `find_relevant`, `get_context`, `get_session`.

## Operations

- Sync job: `~/.agents/memory/memory-sync.ts`, launchd label `com.agent-memory.sync`, every 10 min, log `~/.agents/memory/sync.log`.
- State: `~/.agents/memory/state.json`.
- Never run two Lore indexers at once. The job holds `~/.agents/memory/sync.lock`.
- Before any Lore schema surgery: `sqlite3 ~/.lore/lore.db ".backup ~/.lore/backups/<name>.db"`.
