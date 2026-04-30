#!/usr/bin/env python3
"""
Test the VictoriaMetrics MCP Server (Python).

Usage:
    python test_victoriametrics_mcp.py http://localhost:8085
    python test_victoriametrics_mcp.py http://localhost:8085 --verbose
"""
import asyncio
import json
import sys
import time
from typing import Any

import httpx

VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv


def pheader(t): print(f"\n{'='*70}\n  {t}\n{'='*70}")
def ptest(t): print(f"\n▶ {t}")
def ppass(m=""): print(f"  ✓ PASS{f' - {m}' if m else ''}")
def pfail(m=""): print(f"  ✗ FAIL{f' - {m}' if m else ''}")
def pdetail(k, v):
    if VERBOSE: print(f"    {k}: {v}")


async def run_tests(base_url: str):
    r = {"passed": 0, "failed": 0, "total": 0}
    client = httpx.AsyncClient(timeout=60, verify=False)

    pheader(f"Testing VictoriaMetrics MCP — {base_url}")

    try:
        # 1. Health
        ptest("1. Health Check")
        r["total"] += 1
        try:
            resp = await client.get(f"{base_url}/health")
            ok = resp.status_code == 200
            (ppass if ok else pfail)(f"status={resp.status_code}")
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

        # 2. List tools
        ptest("2. List Tools")
        r["total"] += 1
        try:
            resp = await client.get(f"{base_url}/tools")
            tools = resp.json().get("tools", [])
            ok = len(tools) >= 5
            (ppass if ok else pfail)(f"{len(tools)} tools")
            for t in tools:
                pdetail("tool", t["name"])
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

        # 3. Query: up
        ptest("3. Query: up")
        r["total"] += 1
        try:
            resp = await client.post(f"{base_url}/tools/query",
                json={"arguments": {"query": "up"}})
            data = resp.json()
            ok = data.get("success", False)
            result_data = data.get("result", {})
            result_type = result_data.get("resultType", "?")
            num_results = len(result_data.get("result", []))
            (ppass if ok else pfail)(f"type={result_type} results={num_results} time={data.get('executionTime','?')}s")
            pdetail("preview", json.dumps(result_data)[:300])
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

        # 4. Query: count of active series
        ptest("4. Query: count({__name__=~'.+'})")
        r["total"] += 1
        try:
            resp = await client.post(f"{base_url}/tools/query",
                json={"arguments": {"query": "count({__name__=~'.+'})"}})
            data = resp.json()
            ok = data.get("success", False)
            (ppass if ok else pfail)(f"time={data.get('executionTime','?')}s")
            pdetail("result", json.dumps(data.get("result",{}))[:200])
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

        # 5. Query with application_service filter
        ptest("5. Query: up{application_service='grafana-tempo'}")
        r["total"] += 1
        try:
            resp = await client.post(f"{base_url}/tools/query",
                json={"arguments": {"query": 'up{application_service="grafana-tempo"}'}})
            data = resp.json()
            ok = data.get("success", False)
            num = len(data.get("result", {}).get("result", []))
            (ppass if ok else pfail)(f"results={num}")
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

        # 6. Range query
        ptest("6. Range Query: up (last 5m)")
        r["total"] += 1
        try:
            now = int(time.time())
            resp = await client.post(f"{base_url}/tools/query_range",
                json={"arguments": {"query": "up", "start": str(now-300), "end": str(now), "step": "60"}})
            data = resp.json()
            ok = data.get("success", False)
            (ppass if ok else pfail)(f"time={data.get('executionTime','?')}s")
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

        # 7. Metrics list
        ptest("7. List Metrics")
        r["total"] += 1
        try:
            resp = await client.post(f"{base_url}/tools/metrics",
                json={"arguments": {"limit": 10}})
            data = resp.json()
            ok = data.get("success", False)
            num = len(data.get("result", []))
            (ppass if ok else pfail)(f"metrics={num}")
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

        # 8. Labels
        ptest("8. List Labels")
        r["total"] += 1
        try:
            resp = await client.post(f"{base_url}/tools/labels",
                json={"arguments": {}})
            data = resp.json()
            ok = data.get("success", False)
            num = len(data.get("result", []))
            (ppass if ok else pfail)(f"labels={num}")
            pdetail("labels", data.get("result", [])[:10])
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

        # 9. Label values
        ptest("9. Label Values: job")
        r["total"] += 1
        try:
            resp = await client.post(f"{base_url}/tools/label_values",
                json={"arguments": {"label": "job"}})
            data = resp.json()
            ok = data.get("success", False)
            num = len(data.get("result", []))
            (ppass if ok else pfail)(f"values={num}")
            pdetail("values", data.get("result", [])[:10])
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

        # 10. TSDB Status
        ptest("10. TSDB Status")
        r["total"] += 1
        try:
            resp = await client.post(f"{base_url}/tools/tsdb_status",
                json={"arguments": {"topN": 5}})
            data = resp.json()
            ok = data.get("success", False)
            (ppass if ok else pfail)(f"time={data.get('executionTime','?')}s")
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

        # 11. Alerts
        ptest("11. Alerts")
        r["total"] += 1
        try:
            resp = await client.post(f"{base_url}/tools/alerts",
                json={"arguments": {}})
            data = resp.json()
            ok = data.get("success", False)
            (ppass if ok else pfail)(f"time={data.get('executionTime','?')}s")
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

        # 12. Unknown tool
        ptest("12. Unknown Tool")
        r["total"] += 1
        try:
            resp = await client.post(f"{base_url}/tools/fake",
                json={"arguments": {}})
            data = resp.json()
            ok = data.get("success") is False
            (ppass if ok else pfail)(f"error={data.get('error','?')[:60]}")
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

    finally:
        await client.aclose()

    pheader("Summary")
    print(f"Passed: {r['passed']}/{r['total']} ✓")
    print(f"Failed: {r['failed']}/{r['total']} ✗")
    if r["failed"] == 0:
        print("\n🎉 All tests passed!")
    else:
        print(f"\n⚠️  {r['failed']} test(s) failed")
    return r["failed"] == 0


if __name__ == "__main__":
    url = "http://localhost:8085"
    for arg in sys.argv[1:]:
        if not arg.startswith("-"):
            url = arg
            break
    print(f"URL: {url}\nVerbose: {VERBOSE}\n")
    success = asyncio.run(run_tests(url))
    sys.exit(0 if success else 1)
