#!/usr/bin/env python3
"""Retry Hindsight retains that failed for transient reasons (missing model, rate limit, connection).

Safe to re-run. Per document it retries only the newest failed op, and skips it when the
document already holds newer processed content, so a stale snapshot never overwrites a newer one.
Content errors (BadRequest other than model_not_found, provider safety blocks) are left alone.
"""
import json, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "http://127.0.0.1:8888/v1/default/banks"
BANKS = sys.argv[1:] or ["hermes-memory", "pi-memory", "omp"]
RETRIABLE = ("model_not_found", "RateLimitError", "APIConnectionError", "InternalServerError")


def call(url, method="GET"):
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def log(*parts):
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


def candidates(bank):
    ops, off = [], 0
    while True:
        page = call(f"{API}/{bank}/operations?status=failed&limit=100&offset={off}")["operations"]
        ops += page
        off += 100
        if len(page) < 100:
            break
    kids = [o for o in ops if o["operation_type"] == "retain" and any(k in (o.get("error_message") or "") for k in RETRIABLE)]
    newest = {}
    for o in kids:
        meta = call(f"{API}/{bank}/operations/{o['id']}").get("result_metadata") or {}
        docs = meta.get("document_ids") or [o["id"]]
        for doc in docs:
            if doc not in newest or o["created_at"] > newest[doc]["created_at"]:
                newest[doc] = o
    picked, skipped = {}, 0
    for doc, o in newest.items():
        existing = call(f"{API}/{bank}/documents/{urllib.request.quote(doc, safe='')}")
        if existing and existing["updated_at"] >= o["created_at"]:
            skipped += 1
            continue
        picked[o["id"]] = o
    ops_sorted = sorted(picked.values(), key=lambda o: o["created_at"], reverse=True)
    log(f"{bank}: {len(kids)} retriable failed, {len(newest)} documents, {skipped} already newer, {len(ops_sorted)} to retry")
    return ops_sorted


def retry(bank, op):
    call(f"{API}/{bank}/operations/{op['id']}/retry", "POST")
    deadline = time.time() + 900
    while time.time() < deadline:
        time.sleep(5)
        status = call(f"{API}/{bank}/operations/{op['id']}")
        if status["status"] in ("completed", "failed"):
            return status["status"], (status.get("error_message") or "")[:160]
    return "timeout", ""


def main():
    totals = {}
    for bank in BANKS:
        ops = candidates(bank)
        done = {"completed": 0, "failed": 0, "timeout": 0}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for i, (status, err) in enumerate(pool.map(lambda o: retry(bank, o), ops), 1):
                done[status] += 1
                if status != "completed" or i % 25 == 0:
                    log(f"{bank} {i}/{len(ops)} {status} {err}")
        totals[bank] = done
        log(f"{bank} DONE {done}")
    log("ALL DONE", json.dumps(totals))


if __name__ == "__main__":
    main()
