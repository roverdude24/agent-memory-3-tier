#!/usr/bin/env python3
"""Minimal stdio MCP wrapper for local Hindsight."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

API_URL = os.environ.get("HINDSIGHT_API_URL", "http://127.0.0.1:8888").rstrip("/")
BANK_ID = os.environ.get("HINDSIGHT_BANK_ID", "hermes-memory")
PROTOCOL_VERSION = "2024-11-05"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _request(path: str, payload: Dict[str, Any]) -> Any:
    url = f"{API_URL}{path}"
    data = _json_dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
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


def _bank_path(suffix: str) -> str:
    return f"/v1/default/banks/{urllib.parse.quote(BANK_ID, safe='')}{suffix}"


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
    payload: Dict[str, Any] = {
        "query": query,
        "budget": args.get("budget", "mid"),
        "max_tokens": int(args.get("max_tokens", 4096)),
    }
    for key in ("types", "tags", "tags_match", "trace", "include"):
        if key in args:
            payload[key] = args[key]
    return _request(_bank_path("/memories/recall"), payload)


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
    return _request(_bank_path("/reflect"), payload)


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
    return [
        {"name": "retain", "description": "Store durable information in the configured Hindsight bank.", "inputSchema": text_arg},
        {"name": "recall", "description": "Recall relevant memories from the configured Hindsight bank.", "inputSchema": query_arg},
        {"name": "reflect", "description": "Ask Hindsight to answer using the configured bank.", "inputSchema": query_arg},
        {"name": "learn", "description": "Store a repeated pattern or correction in the configured Hindsight bank.", "inputSchema": text_arg},
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
    assert names == ["retain", "recall", "reflect", "learn"]
    assert bad and bad["error"]["code"] == -32601
    assert malformed and malformed["error"]["code"] == -32602
    print("PASS initialize tools/list malformed-input")
    print("tools: " + ", ".join(names))
    print(f"defaults: api={API_URL} bank={BANK_ID}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Hindsight stdio MCP wrapper")
    parser.add_argument("--self-test", action="store_true", help="run wrapper self-test without calling Hindsight")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    return run_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
