# Install guide: Windows 10/11 (written for an agent to follow)

This is the Windows version of [INSTALL.md](INSTALL.md). The architecture and rules are the same. Only the commands, paths, and scheduler differ. Run everything in **PowerShell 7** (`pwsh`) as the normal user, not as Administrator.

Status: the scripts are patched for Windows, but this guide has not yet been run end to end on a Windows machine. Treat each check as the source of truth and report anything that differs.

## Rules for the installing agent

- Back up every config file before editing it: `Copy-Item <file> "<file>.bak-$(Get-Date -Format yyyyMMddHHmmss)"`.
- Keep existing MCP servers and settings. Add entries; never replace a whole file.
- Never print, commit, or retain the LLM API key.
- Only wire the harnesses that are installed. Skip the others and say so.
- In JSON configs, write paths with forward slashes (`C:/Users/me/...`). In TOML, use single-quoted literal strings.
- Claim only what a check proved.

## Phase 0: prerequisites

```powershell
winget install -e --id OpenJS.NodeJS      # Node 26+
winget install -e --id Oven-sh.Bun
winget install -e --id Python.Python.3.12
winget install -e --id Git.Git
# open a new pwsh window so PATH updates, then:
git clone https://github.com/roverdude24/agent-memory-3-tier "$HOME\projects\agent-memory-3-tier"
Set-Location "$HOME\projects\agent-memory-3-tier"   # run every later command from here
$LORE = "$HOME\.npm-global\node_modules\@jordanhindo\lore\dist\cli\lore.js"
```

Check: `node --version` (v26+), `bun --version`, `py -3.12 --version` all run.

`$LORE` is Lore's JS entry. On Windows, always run Lore as `node $LORE ...`, never through the `lore.cmd` shim: MCP clients and the sync job cannot spawn `.cmd` files directly.

## Phase 1: Lore (tier 1)

```powershell
git clone https://github.com/roverdude24/lore "$HOME\projects\lore"
Push-Location "$HOME\projects\lore"; npm ci; npm run build; Pop-Location
npm install -g --prefix "$HOME\.npm-global" "$HOME\projects\lore"
node $LORE setup
node $LORE embed      # first full embed can take ~20 min for ~75k messages
```

If `npm ci` fails while building `better-sqlite3`, there is no prebuilt binary for this Node version. Install the C++ build tools (`winget install -e --id Microsoft.VisualStudio.2022.BuildTools --override "--quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"`) and rerun `npm ci`.

Check: `node $LORE status` shows a non-zero message count, and `node $LORE search "<a word you typed last week>"` returns hits.

## Phase 2: Hindsight (tier 2)

1. Install the API server. Its embedded PostgreSQL (`pg0-embedded`) ships a Windows wheel:

   ```powershell
   py -3.12 -m venv "$HOME\.hindsight-venv"
   & "$HOME\.hindsight-venv\Scripts\pip.exe" install hindsight-api
   New-Item -ItemType Directory -Force "$HOME\.hindsight" | Out-Null
   ```

2. Ask the user for an OpenAI-compatible LLM base URL, model id, and API key. Verify the model with one real chat-completion request. Store the key only in a user environment variable:

   ```powershell
   [Environment]::SetEnvironmentVariable("HINDSIGHT_API_LLM_API_KEY", "<key>", "User")
   ```

3. Copy `templates/hindsight-start.ps1` to `$HOME\.hindsight\start.ps1`, fill in the model and base URL, and register it to start at logon:

   ```powershell
   Copy-Item templates\hindsight-start.ps1 "$HOME\.hindsight\start.ps1"
   $a = New-ScheduledTaskAction -Execute "conhost.exe" -Argument "--headless pwsh -NoProfile -File `"$HOME\.hindsight\start.ps1`""
   $s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
   Register-ScheduledTask -TaskName "HindsightAPI" -Action $a -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) -Settings $s
   Start-ScheduledTask HindsightAPI
   Start-Sleep 30; Invoke-RestMethod http://127.0.0.1:8888/health
   ```

4. Create one bank per harness. There is no Windows CLI binary, so use the REST API:

   ```powershell
   foreach ($bank in "omp","pi-memory","hermes-memory") {
     Invoke-RestMethod -Method Put "http://127.0.0.1:8888/v1/default/banks/$bank" -ContentType application/json `
       -Body (@{ name = $bank; mission = "Durable facts, preferences and corrections from $bank coding-agent sessions." } | ConvertTo-Json)
   }
   ```

Check: `/health` returns `status = healthy` and `database = connected`. Then retain and recall a marker:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8888/v1/default/banks/omp/memories -ContentType application/json -Body '{"items":[{"content":"install-check marker 7Q2"}],"async":false}'
Invoke-RestMethod -Method Post http://127.0.0.1:8888/v1/default/banks/omp/memories/recall -ContentType application/json -Body '{"query":"install-check marker"}'
```

**Fallback if Hindsight will not run natively:** install it inside WSL2 (Ubuntu) with the Linux steps from [INSTALL.md](INSTALL.md) Phase 2, using `hindsight-linux-amd64` and a systemd user service instead of launchd. WSL2 forwards `127.0.0.1:8888` to Windows, so everything else in this guide stays the same.

## Phase 3: Tolaria (tier 3)

1. Download and run the latest `Tolaria_<version>_x64-setup.exe` from [GitHub releases](https://github.com/refactoringhq/tolaria/releases/latest). Create or open a vault at `$HOME\Documents\Tolaria`.
2. Copy the rules into the vault and create the project folder:

   ```powershell
   Copy-Item MEMORY-RULES.md "$HOME\Documents\Tolaria\agent-memory-rules.md"
   New-Item -ItemType Directory -Force "$HOME\Documents\Tolaria\project" | Out-Null
   ```

3. Find the bundled MCP server:

   ```powershell
   $TOLARIA_MCP = (Get-ChildItem "$env:LOCALAPPDATA","$env:ProgramFiles" -Recurse -Filter index.js -ErrorAction SilentlyContinue |
     Where-Object FullName -match 'Tolaria.*mcp-server' | Select-Object -First 1).FullName
   $TOLARIA_MCP
   ```

Check: `$TOLARIA_MCP` prints a path ending in `mcp-server\index.js`.

## Phase 4: the sync job

```powershell
New-Item -ItemType Directory -Force "$HOME\.agents\memory" | Out-Null
Copy-Item scripts\memory-sync.ts, scripts\pi-memory-bridge.ts, scripts\hindsight-mcp.py "$HOME\.agents\memory\"
bun "$HOME\.agents\memory\memory-sync.ts"      # first run by hand
$bun = (Get-Command bun).Source
$a = New-ScheduledTaskAction -Execute "conhost.exe" -WorkingDirectory "$HOME\.agents\memory" `
  -Argument "--headless cmd.exe /c `"`"$bun`" `"$HOME\.agents\memory\memory-sync.ts`" >> `"$HOME\.agents\memory\sync.log`" 2>&1`""
$t = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName "AgentMemorySync" -Action $a -Trigger $t -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew)
```

`conhost.exe --headless` keeps a console window from flashing every 10 minutes.

Check: the manual run exits 0. After `Start-ScheduledTask AgentMemorySync`, the last line of `sync.log` lists each source with no `FAILED`.

## Phase 5: wire each harness

MCP servers. Substitute your real paths with forward slashes:

| Server | Command | Args | Env |
|---|---|---|---|
| `lore` | `node` | `["C:/Users/<you>/.npm-global/node_modules/@jordanhindo/lore/dist/cli/lore.js", "serve"]` | none |
| `hindsight` | `C:/Users/<you>/.hindsight-venv/Scripts/python.exe` | `["C:/Users/<you>/.agents/memory/hindsight-mcp.py"]` | `HINDSIGHT_API_URL=http://127.0.0.1:8888`, `HINDSIGHT_BANK_ID=<bank>` |
| `tolaria` | `node` | `["<$TOLARIA_MCP with forward slashes>"]` | `WS_UI_PORT=9711` |

Check the Hindsight bridge first: `& "$HOME\.hindsight-venv\Scripts\python.exe" "$HOME\.agents\memory\hindsight-mcp.py" --self-test`.

- **Claude Code:**

  ```powershell
  claude mcp add -s user lore -- node "$LORE" serve
  claude mcp add -s user hindsight -e HINDSIGHT_API_URL=http://127.0.0.1:8888 -e HINDSIGHT_BANK_ID=hermes-memory -- "$HOME\.hindsight-venv\Scripts\python.exe" "$HOME\.agents\memory\hindsight-mcp.py"
  claude mcp add -s user tolaria -e WS_UI_PORT=9711 -- node "$TOLARIA_MCP"
  ```

- **Codex:** append to `$HOME\.codex\config.toml`, using single-quoted TOML strings so backslashes stay literal:

  ```toml
  [mcp_servers.lore]
  command = 'node'
  args = ['C:\Users\<you>\.npm-global\node_modules\@jordanhindo\lore\dist\cli\lore.js', 'serve']

  [mcp_servers.hindsight]
  command = 'C:\Users\<you>\.hindsight-venv\Scripts\python.exe'
  args = ['C:\Users\<you>\.agents\memory\hindsight-mcp.py']
  [mcp_servers.hindsight.env]
  HINDSIGHT_API_URL = 'http://127.0.0.1:8888'
  HINDSIGHT_BANK_ID = 'hermes-memory'

  [mcp_servers.tolaria]
  command = 'node'
  args = ['<TOLARIA_MCP path>']
  [mcp_servers.tolaria.env]
  WS_UI_PORT = '9711'
  ```

- **OMP:** add the three servers to `$HOME\.omp\agent\mcp.json` under `mcpServers` with `HINDSIGHT_BANK_ID=omp`, and add the `memory:` block from [INSTALL.md](INSTALL.md#per-harness) to `$HOME\.omp\agent\config.yml`.
- **Pi:** copy `scripts\pi-memory-bridge.ts` to `$HOME\.pi\agent\extensions\` (copy, not symlink: Windows symlinks need Developer Mode). Add `lore` and `tolaria` to `$HOME\.pi\agent\mcp.json`, and Hindsight as `{"url": "http://127.0.0.1:8888/mcp/pi-memory/"}`.
- **Gemini CLI / Antigravity:** add the three servers to `$HOME\.gemini\settings.json`, or with `agy mcp add`.
- **Hermes:** usually runs in WSL, not native Windows. If it is installed in WSL, follow the macOS/Linux steps there.

Instruction files: paste the block from [AGENTS-snippet.md](AGENTS-snippet.md) into each harness's global instruction file with that harness's bank. On Windows, replace `~/.npm-global/bin/lore search` in the snippet with `node <LORE path> search`.

## Phase 6: verify end to end

The same as [INSTALL.md Phase 6](INSTALL.md#phase-6-verify-end-to-end), using `node $LORE search "K7F3" --mode keyword` and `Start-ScheduledTask AgentMemorySync` to force a sync. Report the same table: harness × (MCP connected, Lore marker found, Hindsight retain seen, instructions loaded).

## Troubleshooting

See [INSTALL.md Troubleshooting](INSTALL.md#troubleshooting). On Windows, restart Hindsight with `Stop-ScheduledTask HindsightAPI; Start-ScheduledTask HindsightAPI`, and run the backlog retry with `& "$HOME\.hindsight-venv\Scripts\python.exe" scripts\hindsight-retry-failed.py <bank>`.
