# Snippet for AGENTS.md / CLAUDE.md / GEMINI.md

Paste the block below into each harness's global instruction file. Replace `<bank>` with that harness's Hindsight bank (see [MEMORY-RULES.md](MEMORY-RULES.md#per-harness-wiring)).

```markdown
# Memory: 3 tiers

Full spec: Tolaria note `~/Documents/Tolaria/agent-memory-rules.md`.

1. **Lore (fast, raw):** every session from every harness, indexed and embedded automatically every 10 min. Never write to it by hand. Search it when past wording, commands, or "how did we solve X" matter: MCP `lore` tools, or `~/.npm-global/bin/lore search "<query>"`. Search is hybrid by default (keyword + meaning, works across languages); add `--mode keyword` (MCP `mode: "keyword"`) for exact terms.
2. **Hindsight (middle, facts):** this harness uses bank `<bank>`. Recall at session start. Use `retain` / `learn` for corrections, preferences, decisions, and reusable fixes. Never retain secrets or progress chatter.
3. **Tolaria (truth):** locked decisions, client facts, and project status in `~/Documents/Tolaria/project/project-<slug>.md`. Read it at session start through MCP `tolaria`. Write only stable truth with `update_note` / `append_to_note`, at milestones or when asked.

- Other agents' Hindsight banks: MCP `hindsight` `recall` takes `banks` (see `list_banks`; `["all"]` = every active bank). Use it only when your own recall has nothing for the task, the user names another agent, or a fact you are about to retain contradicts your bank. Prefer one named bank. Results carry `bank`; confirm other banks' facts in Tolaria or Lore before acting on or retaining them. `retain`/`learn` always write your own bank.
- Conflicts: user > repo `PROJECT-STATE.md` > Tolaria > own Hindsight bank > other agents' banks > Lore. Fix the lower tier when it is wrong.
- Promote a Hindsight fact to Tolaria when the user confirms it or it holds across 3+ sessions.
```

Also copy [MEMORY-RULES.md](MEMORY-RULES.md) into the vault as `~/Documents/Tolaria/agent-memory-rules.md` so the "Full spec" pointer resolves.
