#!/usr/bin/env bun
// 3-tier memory sync (spec: MEMORY-RULES.md in github.com/roverdude24/agent-memory-3-tier).
// Tier 1: index every harness's sessions into Lore (~/.lore/lore.db).
// Tier 2: retain idle Claude/Codex sessions into Hindsight. Pi (bridge), OMP and Hermes retain natively.
// Run by launchd every 10 min (com.agent-memory.sync). Safe to re-run: Lore writes are upserts,
// Hindsight retains use one document_id per session.
import { Database } from "bun:sqlite";
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, renameSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { buildLoreBatch, projectTag, redactSecrets } from "./pi-memory-bridge.ts";

const HOME = homedir();
const DIR = join(HOME, ".agents", "memory");
const STATE = join(DIR, "state.json");
const LOCK = join(DIR, "sync.lock");
const WIN = process.platform === "win32";
// Run Lore's JS entry through node: on Windows npm installs `lore` as a .cmd shim that execFileSync cannot spawn.
const LORE = WIN
  ? join(HOME, ".npm-global", "node_modules", "@jordanhindo", "lore", "dist", "cli", "lore.js")
  : join(HOME, ".npm-global", "bin", "lore");
const LORE_DB = join(HOME, ".lore", "lore.db");
const HINDSIGHT = "http://127.0.0.1:8888";
const RETAIN_BANK = process.env.MEMORY_SYNC_BANK ?? "hermes-memory"; // Claude + Codex share one bank
const IDLE_MS = 30 * 60_000;
const MAX_RETAIN_CHARS = 160_000;
const ENV = WIN ? process.env : { ...process.env, PATH: "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" };

type State = {
  files: Record<string, { size: number; mtimeMs: number; entries: number }>;
  retained: Record<string, string>;
  retainSince?: string;
};

const state: State = existsSync(STATE) ? JSON.parse(readFileSync(STATE, "utf8")) : { files: {}, retained: {} };
const summary: string[] = [];
let failed = false;

function lock(): boolean {
  if (existsSync(LOCK)) {
    const pid = Number(readFileSync(LOCK, "utf8"));
    try { process.kill(pid, 0); return false; } catch { /* stale lock */ }
  }
  writeFileSync(LOCK, String(process.pid));
  return true;
}

function saveState() {
  writeFileSync(`${STATE}.tmp`, JSON.stringify(state));
  renameSync(`${STATE}.tmp`, STATE);
}

// Tier 1a: harnesses Lore has a native reader for.
function loreIndex(dir: string, source: string) {
  if (!existsSync(dir)) return;
  try {
    const out = execFileSync("node", [LORE, "index", dir, "--source", source], { env: ENV, timeout: 30 * 60_000, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
    summary.push(`${source}: ${out.trim().replace(/^Indexed /, "")}`);
  } catch (error) {
    failed = true;
    summary.push(`${source}: FAILED ${String(error).slice(0, 200)}`);
  }
}

// Tier 1c: embed new user/assistant messages so Lore's hybrid search covers them (incremental, newest first).
function loreEmbed() {
  try {
    const out = execFileSync("node", [LORE, "embed"], { env: ENV, timeout: 30 * 60_000, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
    summary.push(`embed: ${out.trim().replace(/^Embedded /, "")}`);
  } catch (error) {
    failed = true;
    summary.push(`embed: FAILED ${String(error).slice(0, 200)}`);
  }
}

// Tier 1b: Pi-format transcripts (Pi, OMP) via the Pi bridge's record builder. Main sessions only, like `lore index`.
type Header = { type: "session"; id: string; cwd: string };
const isHeader = (value: unknown): value is Header =>
  !!value && typeof value === "object" && "type" in value && value.type === "session" && "id" in value && typeof value.id === "string";

function pushPiFormat(root: string, source: string) {
  if (!existsSync(root)) return;
  let pushed = 0, messages = 0, errors = 0;
  for (const project of readdirSync(root, { withFileTypes: true })) {
    if (!project.isDirectory()) continue;
    for (const file of readdirSync(join(root, project.name), { withFileTypes: true })) {
      if (!file.isFile() || !file.name.endsWith(".jsonl")) continue;
      const path = join(root, project.name, file.name);
      const { size, mtimeMs } = statSync(path);
      const prev = state.files[path];
      if (prev && prev.size === size && prev.mtimeMs === mtimeMs) continue;
      try {
        const lines: unknown[] = readFileSync(path, "utf8").split("\n").filter(Boolean).map((line) => {
          try { return JSON.parse(line); } catch { return null; }
        });
        const header = lines.find(isHeader);
        if (!header) continue;
        const entries = lines.filter((line) => line && !isHeader(line) && !(typeof line === "object" && "type" in line && line.type === "title"));
        const from = prev && prev.size <= size ? Math.min(prev.entries, entries.length) : 0;
        const batch = buildLoreBatch({
          sessionId: header.id, sessionFile: path, cwd: header.cwd, size, mtime: new Date(mtimeMs).toISOString(),
          seqOffset: from, totalEntries: entries.length, source,
        }, entries.slice(from));
        if (batch.messages.length) {
          execFileSync("node", [LORE, "push"], { env: ENV, input: JSON.stringify(batch), timeout: 120_000, stdio: ["pipe", "ignore", "pipe"] });
          pushed++;
          messages += batch.messages.length;
        }
        state.files[path] = { size, mtimeMs, entries: entries.length };
      } catch (error) {
        errors++;
        if (errors <= 3) summary.push(`${source}: push failed ${path}: ${String(error).slice(0, 160)}`);
      }
    }
    saveState();
  }
  if (errors) failed = true;
  summary.push(`${source}: pushed ${pushed} files, ${messages} messages, ${errors} errors`);
}

// Tier 2: Lore -> Hindsight for harnesses without native retain.
async function retainIdle(sources: string[]) {
  state.retainSince ??= new Date(Date.now() - 14 * 86_400_000).toISOString(); // first run: skip old history
  const cutoff = new Date(Date.now() - IDLE_MS).toISOString();
  // Not readonly: a readonly connection cannot create the WAL -shm file, so it fails whenever no other Lore process is open.
  const db = new Database(LORE_DB, { readwrite: true, create: false });
  const sessions = db.query(
    `SELECT session_id, source, project, last_timestamp FROM sessions
     WHERE source IN (${sources.map(() => "?").join(",")}) AND last_timestamp >= ? AND last_timestamp <= ?`,
  ).all(...sources, state.retainSince, cutoff) as Array<{ session_id: string; source: string; project: string | null; last_timestamp: string }>;
  let retained = 0;
  for (const session of sessions) {
    if (state.retained[session.session_id] === session.last_timestamp) continue;
    const rows = db.query("SELECT role, text, timestamp FROM messages WHERE session_id = ? AND role IN ('user','assistant') ORDER BY seq")
      .all(session.session_id) as Array<{ role: string; text: string; timestamp: string | null }>;
    const conversation = rows.map((row) => ({ role: row.role, content: redactSecrets(row.text).slice(0, 40_000), timestamp: row.timestamp }));
    if (!conversation.some((message) => message.role === "user")) { state.retained[session.session_id] = session.last_timestamp; continue; }
    // ponytail: keep first prompt + most recent turns under the cap, same bound as the Pi bridge.
    let chars = conversation.reduce((sum, message) => sum + message.content.length, 0);
    while (chars > MAX_RETAIN_CHARS && conversation.length > 2) chars -= conversation.splice(1, 1)[0].content.length;
    const project = session.project ?? "unknown";
    const response = await fetch(`${HINDSIGHT}/v1/default/banks/${RETAIN_BANK}/memories`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: AbortSignal.timeout(15_000),
      body: JSON.stringify({
        items: [{
          content: JSON.stringify(conversation),
          context: `${session.source} coding-agent session in ${project}; extract durable facts only`,
          document_id: `${session.source}-session:${session.session_id}`,
          timestamp: conversation[0]?.timestamp ?? session.last_timestamp,
          tags: [`source:${session.source}`, projectTag(project)],
          metadata: { source: session.source, session_id: session.session_id, project },
        }],
        async: true,
      }),
    });
    if (!response.ok) { failed = true; summary.push(`retain ${session.session_id}: HTTP ${response.status}`); continue; }
    state.retained[session.session_id] = session.last_timestamp;
    retained++;
  }
  db.close();
  saveState();
  summary.push(`hindsight: retained ${retained} ${sources.join("+")} sessions into ${RETAIN_BANK}`);
}

if (!lock()) {
  console.log(`${new Date().toISOString()} skip: previous run still active`);
  process.exit(0);
}
try {
  loreIndex(join(HOME, ".claude", "projects"), "claude-code");
  loreIndex(join(HOME, ".codex", "sessions"), "codex");
  loreIndex(join(HOME, ".hermes"), "hermes");
  loreIndex(join(HOME, ".gemini", "antigravity", "brain"), "antigravity");
  pushPiFormat(join(HOME, ".omp", "agent", "sessions"), "omp");
  pushPiFormat(join(HOME, ".pi", "agent", "sessions"), "pi");
  loreEmbed();
  await retainIdle(["claude-code", "codex"]).catch((error) => { failed = true; summary.push(`hindsight: FAILED ${String(error).slice(0, 200)}`); });
} finally {
  saveState();
  unlinkSync(LOCK);
  console.log(`${new Date().toISOString()} ${summary.join(" | ")}`);
}
process.exit(failed ? 1 : 0);
