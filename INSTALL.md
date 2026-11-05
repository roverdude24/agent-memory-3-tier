# Install guide (written for an agent to follow)

Hand this file to your coding agent: "Follow INSTALL.md from github.com/roverdude24/agent-memory-3-tier." Each phase ends with a check. Stop at the first failed check and report it; do not continue on a broken tier.

Target: macOS on Apple Silicon with Homebrew. Linux works with the same scripts; replace the launchd steps with a systemd user timer or cron entry.

## Rules for the installing agent

- Back up every config file before editing it (`cp <file> <file>.bak-$(date +%Y%m%d%H%M%S)`).
- Keep existing MCP servers and settings. Add entries; never replace a whole file.
- Never print, commit, or retain the LLM API key.
- Only wire the harnesses that are installed. Skip the others and say so.
- Claim only what a check proved.

## Phase 0: prerequisites

```bash
brew install node bun python@3.12 sqlite
git clone https://github.com/roverdude24/agent-memory-3-tier ~/projects/agent-memory-3-tier
cd ~/projects/agent-memory-3-tier   # run every later command from here
node --version   # Lore's native SQLite module is built for the Node you build it with; rebuild after a Node major upgrade
```

Check: `node`, `bun`, and `/opt/homebrew/bin/python3.12` all run.

## Phase 1: Lore (tier 1, raw session index)

```bash
git clone https://github.com/roverdude24/lore ~/projects/lore
(cd ~/projects/lore && npm ci && npm run build)
npm install -g --prefix ~/.npm-global ~/projects/lore   # always pass --prefix
~/.npm-global/bin/lore setup                            # detects harnesses, backfills history, verifies search
~/.npm-global/bin/lore embed                            # first full embed can take ~20 min for ~75k messages
```

Check: `~/.npm-global/bin/lore status` shows a non-zero message count, and `~/.npm-global/bin/lore search "<a word you typed last week>"` returns hits.

The fork adds hybrid search (multilingual `Xenova/multilingual-e5-small` embeddings) and an Antigravity adapter on top of upstream [jordanhindo/lore](https://github.com/jordanhindo/lore).

## Phase 2: Hindsight (tier 2, extracted facts)

1. Install the CLI and the API server:

   ```bash
   mkdir -p ~/.local/bin ~/.hindsight
   curl -sL -o ~/.local/bin/hindsight https://github.com/vectorize-io/hindsight/releases/latest/download/hindsight-darwin-arm64
   chmod +x ~/.local/bin/hindsight
   /opt/homebrew/bin/python3.12 -m venv ~/.hindsight-venv
   ~/.hindsight-venv/bin/pip install hindsight-api
   ```

2. Ask the user for an OpenAI-compatible LLM endpoint, model id, and API key. Store the key only in `~/.hindsight/.env`:

   ```bash
   printf 'export HINDSIGHT_API_LLM_API_KEY=%s\n' "<key>" > ~/.hindsight/.env && chmod 600 ~/.hindsight/.env
   ```

3. Copy `templates/hindsight-start.sh` to `~/.hindsight/start.sh` and fill in the model and base URL. Verify the model with one real chat-completion request; a model list alone proves nothing.

4. Start it under launchd:

   ```bash
   sed "s#__HOME__#$HOME#g" templates/com.vectorize.hindsight-api.plist > ~/Library/LaunchAgents/com.vectorize.hindsight-api.plist
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vectorize.hindsight-api.plist
   sleep 20 && curl -s http://127.0.0.1:8888/health   # first start loads ML models
   ```

5. Create one bank per harness you use:

   ```bash
   for bank in omp pi-memory hermes-memory; do
     ~/.local/bin/hindsight bank create "$bank" --name "$bank" --mission "Durable facts, preferences and corrections from $bank coding-agent sessions."
   done
   ```

Check: `/health` returns `"status":"healthy"` and `"database":"connected"`. Then `hindsight memory retain omp "install-check marker 7Q2"` followed by `hindsight memory recall omp "install-check marker"` returns the marker.

## Phase 3: Tolaria (tier 3, truth)

1. Install Tolaria from [tolaria.md](https://tolaria.md) (source: [refactoringhq/tolaria](https://github.com/refactoringhq/tolaria)) and create or open a vault at `~/Documents/Tolaria`.
2. Copy `MEMORY-RULES.md` into the vault as `~/Documents/Tolaria/agent-memory-rules.md`.
3. Create `~/Documents/Tolaria/project/` for per-project notes named `project-<slug>.md`, where `<slug>` is the lowercase folder name of the repo.

Check: `/Applications/Tolaria.app/Contents/Resources/mcp-server/index.js` exists.

## Phase 4: the sync job (feeds tiers 1 and 2)

```bash
mkdir -p ~/.agents/memory
cp scripts/memory-sync.ts scripts/pi-memory-bridge.ts ~/.agents/memory/
bun ~/.agents/memory/memory-sync.ts             # first run by hand
sed "s#__HOME__#$HOME#g" templates/com.agent-memory.sync.plist > ~/Library/LaunchAgents/com.agent-memory.sync.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.agent-memory.sync.plist
```

The job, every 10 minutes:

1. Indexes Claude Code, Codex, Hermes, and Antigravity sessions with `lore index` (missing harness folders are skipped).
2. Pushes OMP and Pi sessions with `lore push`.
3. Runs `lore embed` for new messages.
4. Retains Claude Code and Codex sessions idle for 30 min into Hindsight bank `hermes-memory` (override with env `MEMORY_SYNC_BANK`). Secrets are redacted before retain.

Check: the manual run exits 0, and `tail -1 ~/.agents/memory/sync.log` shows a line per source with no `FAILED`.

## Phase 5: wire each harness

Use absolute paths; agents do not load your shell PATH. Substitute your real `$HOME`.

### MCP servers (all harnesses)

| Server | Command | Env |
|---|---|---|
| `lore` | `~/.npm-global/bin/lore serve` | `PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin` |
| `hindsight` | `/opt/homebrew/bin/python3.12 ~/.agents/memory/hindsight-mcp.py` | `HINDSIGHT_API_URL=http://127.0.0.1:8888`, `HINDSIGHT_BANK_ID=<bank>` |
| `tolaria` | `/opt/homebrew/bin/node /Applications/Tolaria.app/Contents/Resources/mcp-server/index.js` | `WS_UI_PORT=9711` |

Copy the Hindsight bridge first: `cp scripts/hindsight-mcp.py ~/.agents/memory/` and run `python3.12 ~/.agents/memory/hindsight-mcp.py --self-test`. HTTP-capable clients may use Hindsight's built-in endpoint `http://127.0.0.1:8888/mcp/<bank>/` instead.

### Per harness

- **OMP:** add the three servers to `~/.omp/agent/mcp.json` under `mcpServers` with `HINDSIGHT_BANK_ID=omp`. Enable native memory in `~/.omp/agent/config.yml`:

  ```yaml
  memory:
    backend: hindsight
    hindsight:
      apiUrl: http://127.0.0.1:8888
      bankId: omp
      scoping: per-project-tagged
      autoRecall: true
  ```

- **Pi:** `ln -s ~/.agents/memory/pi-memory-bridge.ts ~/.pi/agent/extensions/pi-memory-bridge.ts`. The bridge pushes every turn to Lore, retains into `pi-memory`, and injects Hindsight, Lore, and Tolaria context on the first turns. Add `lore` and `tolaria` to `~/.pi/agent/mcp.json`, and Hindsight as `{"url": "http://127.0.0.1:8888/mcp/pi-memory/"}`.
- **Claude Code:**

  ```bash
  claude mcp add -s user lore -e PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin -- $HOME/.npm-global/bin/lore serve
  claude mcp add -s user hindsight -e HINDSIGHT_API_URL=http://127.0.0.1:8888 -e HINDSIGHT_BANK_ID=hermes-memory -- /opt/homebrew/bin/python3.12 $HOME/.agents/memory/hindsight-mcp.py
  claude mcp add -s user tolaria -e WS_UI_PORT=9711 -- /opt/homebrew/bin/node /Applications/Tolaria.app/Contents/Resources/mcp-server/index.js
  ```

- **Codex:** append to `~/.codex/config.toml`:

  ```toml
  [mcp_servers.lore]
  command = "/Users/<you>/.npm-global/bin/lore"
  args = ["serve"]
  [mcp_servers.lore.env]
  PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

  [mcp_servers.hindsight]
  command = "/opt/homebrew/bin/python3.12"
  args = ["/Users/<you>/.agents/memory/hindsight-mcp.py"]
  [mcp_servers.hindsight.env]
  HINDSIGHT_API_URL = "http://127.0.0.1:8888"
  HINDSIGHT_BANK_ID = "hermes-memory"

  [mcp_servers.tolaria]
  command = "/opt/homebrew/bin/node"
  args = ["/Applications/Tolaria.app/Contents/Resources/mcp-server/index.js"]
  [mcp_servers.tolaria.env]
  WS_UI_PORT = "9711"
  ```

- **Hermes:** set the memory provider to Hindsight with bank `hermes-memory`, and add the three MCP servers to every Hermes profile, not only the default one.
- **Gemini CLI / Antigravity:** add the three servers to `~/.gemini/settings.json` (Gemini CLI) or with `agy mcp add --env KEY=VALUE <name> <command> <args>` (Antigravity). Watch for trailing commas: one blocks the whole file from parsing.

### Instruction files

Paste the block from [AGENTS-snippet.md](AGENTS-snippet.md) into each harness's global instruction file (`~/.omp/agent/AGENTS.md`, `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.gemini/GEMINI.md`, and the Hermes profile instructions) with that harness's bank. Run every command in this guide from the repo root.

## Phase 6: verify end to end

Config files prove nothing until a client loads them. For each wired harness:

1. Open a fresh session so the harness reloads its config. List MCP servers; `lore`, `hindsight`, and `tolaria` must be connected.
2. Type a unique marker, for example `memory-check <harness> K7F3`.
3. After the next sync (or `launchctl kickstart gui/$(id -u)/com.agent-memory.sync`), `~/.npm-global/bin/lore search "K7F3" --mode keyword` finds it with the right source.
4. Ask the agent: "Which three memory tiers do you use, and what is your Hindsight bank?" It must answer from its instruction file.
5. Claude Code and Codex: after 30 idle minutes plus one sync, Hindsight holds a document `<source>-session:<id>` in `hermes-memory`.

Report a table: harness × (MCP connected, Lore marker found, Hindsight retain seen, instructions loaded).

## Troubleshooting

- **`lore search` says unknown command:** a different `lore` (the Python SDK) is first in PATH. Use `~/.npm-global/bin/lore`.
- **Lore native module error:** rebuild with the current Node: `cd ~/projects/lore && npm ci && npm run build`.
- **Hindsight will not start, pg0 says "running":** the PID in `~/.pg0/instances/hindsight/instance.json` was reused by another app. Confirm no PostgreSQL listener and no `postmaster.pid`, then set that `pid` to a PID that does not exist. Never kill the recorded PID and never delete the data directory.
- **Hindsight retains fail on every retry with "Provider returned no choices":** the content trips the provider's safety filter. Switch the extraction model; retrying will not help.
- **Hindsight is "healthy" but nothing new gets remembered:** `/health` only checks the API and database, not the extraction model. Check failed operations per bank: `curl -s "http://127.0.0.1:8888/v1/default/banks/<bank>/operations?status=failed&limit=5"`. `model_not_found` means your provider dropped the model in `start.sh`. Test a replacement with one real chat-completion request, put it in `start.sh`, restart Hindsight (`launchctl kickstart -k gui/$(id -u)/com.vectorize.hindsight-api`), then re-send the backlog with `python3.12 scripts/hindsight-retry-failed.py [bank ...]` (one process per bank runs them in parallel). It retries only transient failures, only the newest failed op per document, and skips documents that already hold newer content. Check failed operations weekly; a dropped model fails without telling you.
- **Sync log shows `hindsight: FAILED SQLiteError: unable to open database file`:** you are running an old `memory-sync.ts` that opened Lore's WAL database read-only. Copy the current `scripts/memory-sync.ts`.
- **Search misses a term with punctuation:** Lore's full-text index keeps `_-.` inside words. Retry with the trailing punctuation or a nearby phrase.
