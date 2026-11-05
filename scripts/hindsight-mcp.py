#!/usr/bin/env python3
"""Minimal stdio MCP wrapper for local Hindsight.

Writes (retain, learn) always go to HINDSIGHT_BANK_ID. Reads may span banks: recall takes
`banks` (list of bank ids, or "all"), reflect takes `bank`, and list_banks shows what exists.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

API_URL = os.environ.get("HINDSIGHT_API_URL", "http://127.0.0.1:8888").rstrip("/")
BANK_ID = os.environ.get("HINDSIGHT_BANK_ID", "hermes-memory")
# Banks no harness writes to any more. "all" skips them; naming one explicitly still reads it.
ARCHIVED_BANKS = {b for b in os.environ.get("HINDSIGHT_ARCHIVED_BANKS", "omp-memory").split(",") if b}
PROTOCOL_VERSION = "2024-11-05"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _request(path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
    url = f"{API_URL}{path}"
    req = urllib.request.Request(
        url,
        data=None if payload is None else _json_dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Hindsight HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Hindsight connection failed: {exc.reason}") from exc


def _bank_path(suffix: str, bank: str = BANK_ID) -> str:
    return f"/v1/default/banks/{urllib.parse.quote(bank, safe='')}{suffix}"


def _list_banks() -> List[Dict[str, Any]]:
    data = _request("/v1/default/banks")
    banks = data.get("banks", data) if isinstance(data, dict) else data
    ids = [b.get("bank_id") or b.get("id") for b in banks]
    return [{"bank_id": i, "name": b.get("name"), "own": i == BANK_ID, "archived": i in ARCHIVED_BANKS} for i, b in zip(ids, banks)]


def _resolve_banks(value: Any) -> List[str]:
    names = [value] if isinstance(value, str) else list(value or [])
    if "all" in names:
        names = [b["bank_id"] for b in _list_banks() if not b["archived"]] + [n for n in names if n != "all"]
    names = list(dict.fromkeys(n for n in names if n))  # dedupe, keep order
    return names or [BANK_ID]


def _require_text(args: Dict[str, Any], *names: str) -> str:
    for name in names:
        value = args.get(name)
        if isinstance(value, str) and value.strip():
            return value
    raise ValueError(f"missing required string argument: {' or '.join(names)}")


def _retain(args: Dict[str, Any], *, learning: bool = False) -> Any:
    content = _require_text(args, "content", "text", "memory")
    context = args.get("context") if isinstance(args.get("context"), str) else ""
    if learning:
        context = "learn: repeated pattern/correction" + (f"; {context}" if context else "")
    item: Dict[str, Any] = {"content": content}
    if context:
        item["context"] = context
    doc_id = args.get("document_id") or args.get("doc_id")
    if isinstance(doc_id, str) and doc_id.strip():
        item["document_id"] = doc_id
    tags = args.get("tags")
    if isinstance(tags, list) and all(isinstance(tag, str) for tag in tags):
        item["tags"] = tags
    timestamp = args.get("timestamp")
    if isinstance(timestamp, str) and timestamp.strip():
        item["timestamp"] = timestamp
    return _request(_bank_path("/memories"), {"items": [item], "async": False})


def _recall(args: Dict[str, Any]) -> Any:
    query = _require_text(args, "query")
    total_tokens = int(args.get("max_tokens", 4096))

    def one(bank: str, max_tokens: int) -> Any:
        payload: Dict[str, Any] = {"query": query, "budget": args.get("budget", "mid"), "max_tokens": max_tokens}
        for key in ("types", "tags", "tags_match", "trace", "include"):
            if key in args:
                payload[key] = args[key]
        return _request(_bank_path("/memories/recall", bank), payload)

    if args.get("banks") in (None, "", []):
        return one(BANK_ID, total_tokens)  # unchanged legacy response

    errors: Dict[str, str] = {}
    try:
        banks = _resolve_banks(args["banks"])
    except Exception as exc:
        banks, errors["list_banks"] = [BANK_ID], str(exc)[:300]
    # Own bank keeps its full budget so reading others never starves it; others share a second budget.
    others = [b for b in banks if b != BANK_ID]
    per_other = max(512, total_tokens // max(1, len(others)))
    with ThreadPoolExecutor(max_workers=len(banks)) as pool:
        futures = {b: pool.submit(one, b, total_tokens if b == BANK_ID else per_other) for b in banks}
    per_bank: Dict[str, List[Dict[str, Any]]] = {}
    for bank, future in futures.items():
        try:
            per_bank[bank] = [{**item, "bank": bank} for item in future.result().get("results", [])]
        except Exception as exc:
            errors[bank] = str(exc)[:300]
    # Scores are not comparable across banks of different sizes, so interleave by per-bank rank, own bank first.
    order = sorted(per_bank, key=lambda b: b != BANK_ID)
    results = [per_bank[b][i] for i in range(max(map(len, per_bank.values()), default=0)) for b in order if i < len(per_bank[b])]
    return {"banks": banks, "own_bank": BANK_ID, "results": results, "errors": errors}


def _reflect(args: Dict[str, Any]) -> Any:
    query = _require_text(args, "query")
    payload: Dict[str, Any] = {
        "query": query,
        "budget": args.get("budget", "low"),
        "max_tokens": int(args.get("max_tokens", 4096)),
    }
    for key in ("context", "include", "tags", "tags_match", "fact_types"):
        if key in args:
            payload[key] = args[key]
    return _request(_bank_path("/reflect", args.get("bank") or BANK_ID), payload)


def _tool_schema() -> list[Dict[str, Any]]:
    text_arg = {
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "context": {"type": "string"},
            "document_id": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["content"],
        "additionalProperties": True,
    }
    query_arg = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "budget": {"type": "string"},
            "max_tokens": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["query"],
        "additionalProperties": True,
    }
    recall_arg = {**query_arg, "properties": {**query_arg["properties"], "banks": {
        "type": "array", "items": {"type": "string"},
        "description": f'Banks to read. Omit for this agent\'s own bank ("{BANK_ID}"). ["all"] reads every active bank (archived ones only when named). Results carry "bank". See list_banks.',
    }}}
    reflect_arg = {**query_arg, "properties": {**query_arg["properties"], "bank": {
        "type": "string", "description": f'Bank to reflect over. Omit for this agent\'s own bank ("{BANK_ID}").',
    }}}
    return [
        {"name": "retain", "description": f"Store durable information in this agent's Hindsight bank ({BANK_ID}).", "inputSchema": text_arg},
        {"name": "recall", "description": "Recall relevant memories. Reads this agent's bank by default; pass banks (or [\"all\"]) to also read other agents' banks.", "inputSchema": recall_arg},
        {"name": "reflect", "description": "Ask Hindsight to answer from one bank (this agent's by default).", "inputSchema": reflect_arg},
        {"name": "learn", "description": f"Store a repeated pattern or correction in this agent's Hindsight bank ({BANK_ID}).", "inputSchema": text_arg},
        {"name": "list_banks", "description": "List every Hindsight bank (one per agent harness) so recall can read across them.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    ]


def _call_tool(name: str, args: Dict[str, Any]) -> Any:
    if name == "retain":
        return _retain(args)
    if name == "recall":
        return _recall(args)
    if name == "reflect":
        return _reflect(args)
    if name == "learn":
        return _retain(args, learning=True)
    if name == "list_banks":
        return _list_banks()
    raise ValueError(f"unknown tool: {name}")


def _success(message_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def handle(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}

    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return _success(message_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "hindsight", "version": "0.1.0"},
        })
    if method == "tools/list":
        return _success(message_id, {"tools": _tool_schema()})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if not isinstance(name, str):
            return _error(message_id, -32602, "tools/call requires a string tool name")
        try:
            result = _call_tool(name, args)
            return _success(message_id, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}], "isError": False})
        except Exception as exc:  # MCP surfaces tool errors as content.
            return _success(message_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
    return _error(message_id, -32601, f"unknown method: {method}")


def read_message(stdin: Any) -> Optional[Dict[str, Any]]:
    line = stdin.buffer.readline()
    if not line:
        return None
    text = line.decode("utf-8", "replace").strip()
    if not text:
        raise ValueError("empty JSON-RPC line")
    return json.loads(text)


def write_message(stdout: Any, message: Dict[str, Any]) -> None:
    stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    stdout.flush()


def run_stdio() -> int:
    while True:
        try:
            message = read_message(sys.stdin)
            if message is None:
                return 0
            response = handle(message)
            if response is not None:
                write_message(sys.stdout, response)
        except Exception as exc:
            write_message(sys.stdout, _error(None, -32700, str(exc)))
    return 0


def self_test() -> int:
    init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    tools = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    bad = handle({"jsonrpc": "2.0", "id": 3, "method": "missing/method", "params": {}})
    malformed = handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"arguments": {}}})
    names = [tool["name"] for tool in tools["result"]["tools"]]
    assert init and init["result"]["serverInfo"]["name"] == "hindsight"
    assert names == ["retain", "recall", "reflect", "learn", "list_banks"]
    assert bad and bad["error"]["code"] == -32601
    assert malformed and malformed["error"]["code"] == -32602
    print("PASS initialize tools/list malformed-input")
    _self_test_fanout()
    print("PASS cross-bank recall: own budget kept, archived skipped, dedupe, interleave, per-bank errors")
    print("tools: " + ", ".join(names))
    print(f"defaults: api={API_URL} bank={BANK_ID}")
    return 0


def _self_test_fanout() -> None:
    global _request, ARCHIVED_BANKS
    real_request, real_archived = _request, ARCHIVED_BANKS
    asked: Dict[str, int] = {}
    sizes = {BANK_ID: 3, "other": 2}

    def fake(path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        if payload is None:
            return {"banks": [{"bank_id": b} for b in (BANK_ID, "other", "broken", "old")]}
        bank = urllib.parse.unquote(path.split("/banks/")[1].split("/")[0])
        if bank == "broken":
            raise RuntimeError("Hindsight HTTP 500")
        asked[bank] = payload["max_tokens"]
        return {"results": [{"id": f"{bank}-{i}"} for i in range(sizes[bank])]}

    _request, ARCHIVED_BANKS = fake, {"old"}
    try:
        legacy = _recall({"query": "q", "max_tokens": 2000})
        merged = _recall({"query": "q", "max_tokens": 2000, "banks": ["all", "other"]})
    finally:
        _request, ARCHIVED_BANKS = real_request, real_archived
    assert "bank" not in legacy["results"][0] and "errors" not in legacy
    assert merged["banks"] == [BANK_ID, "other", "broken"], merged["banks"]  # "old" archived, "other" deduped
    assert asked == {BANK_ID: 2000, "other": 1000}, asked  # own keeps full budget; 2 others share 2000
    assert [r["id"] for r in merged["results"]] == [f"{BANK_ID}-0", "other-0", f"{BANK_ID}-1", "other-1", f"{BANK_ID}-2"]
    assert set(merged["errors"]) == {"broken"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Hindsight stdio MCP wrapper")
    parser.add_argument("--self-test", action="store_true", help="run wrapper self-test without calling Hindsight")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    return run_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
