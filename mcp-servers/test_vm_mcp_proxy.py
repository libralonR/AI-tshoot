#!/usr/bin/env python3
"""
Test the VM MCP Proxy adapter.
Tests the REST /tools/{name} interface that translates to MCP protocol.

Usage:
    # Test via REST (requires proxy + VM MCP running)
    python test_vm_mcp_proxy.py http://localhost:8084

    # With verbose output
    python test_vm_mcp_proxy.py http://localhost:8084 --verbose
"""
import asyncio
import json
import sys
from typing import Any, Dict

import httpx

VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv


def print_header(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_test(name: str):
    print(f"\n▶ {name}")


def print_result(success: bool, message: str = ""):
    status = "✓ PASS" if success else "✗ FAIL"
    print(f"  {status}", end="")
    if message:
        print(f" - {message}")
    else:
        print()


def print_detail(key: str, value: Any):
    if VERBOSE:
        print(f"    {key}: {value}")


async def test_vm_mcp_proxy(base_url: str):
    """Test the VM MCP Proxy REST endpoints."""
    client = httpx.AsyncClient(timeout=30, verify=False)
    results = {"passed": 0, "failed": 0, "total": 0}

    print_header("Testing VM MCP Proxy Adapter")
    print(f"Base URL: {base_url}")

    try:
        # ------------------------------------------------------------------
        # Test 1: Health check
        # ------------------------------------------------------------------
        print_test("Test 1: Health Check")
        results["total"] += 1
        try:
            r = await client.get(f"{base_url}/health")
            data = r.json()
            success = r.status_code == 200 and data.get("status") in ("ok", "degraded")
            print_result(success, f"status={data.get('status')} | upstream_healthy={data.get('upstream_healthy')}")
            print_detail("upstream", data.get("upstream"))
            results["passed" if success else "failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            results["failed"] += 1

        # ------------------------------------------------------------------
        # Test 2: List tools
        # ------------------------------------------------------------------
        print_test("Test 2: List Available Tools")
        results["total"] += 1
        try:
            r = await client.get(f"{base_url}/tools")
            data = r.json()
            tools = data.get("tools", [])
            success = len(tools) >= 5
            print_result(success, f"Found {len(tools)} tools")
            for t in tools:
                name = t.get("name", "?")
                desc = t.get("description", "")[:60]
                print_detail("tool", f"{name}: {desc}...")
            results["passed" if success else "failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            results["failed"] += 1

        # ------------------------------------------------------------------
        # Test 3: Instant query — up
        # ------------------------------------------------------------------
        print_test("Test 3: Instant Query (up)")
        results["total"] += 1
        try:
            r = await client.post(
                f"{base_url}/tools/query",
                json={"arguments": {"query": "up"}},
            )
            data = r.json()
            success = data.get("success", False) or "result" in data
            print_result(success, f"executionTime={data.get('executionTime', '?')}s")
            if VERBOSE and "result" in data:
                result_str = json.dumps(data["result"], default=str)
                print_detail("result_size", f"{len(result_str)} bytes")
            results["passed" if success else "failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            results["failed"] += 1

        # ------------------------------------------------------------------
        # Test 4: Instant query — count of metrics
        # ------------------------------------------------------------------
        print_test("Test 4: Instant Query (count of active series)")
        results["total"] += 1
        try:
            r = await client.post(
                f"{base_url}/tools/query",
                json={"arguments": {"query": "count({__name__=~'.+'})"}},
            )
            data = r.json()
            success = data.get("success", False) or "result" in data
            print_result(success, f"executionTime={data.get('executionTime', '?')}s")
            results["passed" if success else "failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            results["failed"] += 1

        # ------------------------------------------------------------------
        # Test 5: List metrics
        # ------------------------------------------------------------------
        print_test("Test 5: List Metrics")
        results["total"] += 1
        try:
            r = await client.post(
                f"{base_url}/tools/metrics",
                json={"arguments": {"limit": 10}},
            )
            data = r.json()
            success = data.get("success", False) or "result" in data
            print_result(success, f"executionTime={data.get('executionTime', '?')}s")
            results["passed" if success else "failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            results["failed"] += 1

        # ------------------------------------------------------------------
        # Test 6: List labels
        # ------------------------------------------------------------------
        print_test("Test 6: List Labels")
        results["total"] += 1
        try:
            r = await client.post(
                f"{base_url}/tools/labels",
                json={"arguments": {}},
            )
            data = r.json()
            success = data.get("success", False) or "result" in data
            print_result(success, f"executionTime={data.get('executionTime', '?')}s")
            results["passed" if success else "failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            results["failed"] += 1

        # ------------------------------------------------------------------
        # Test 7: Label values for __name__
        # ------------------------------------------------------------------
        print_test("Test 7: Label Values (__name__, limit 10)")
        results["total"] += 1
        try:
            r = await client.post(
                f"{base_url}/tools/label_values",
                json={"arguments": {"label": "__name__"}},
            )
            data = r.json()
            success = data.get("success", False) or "result" in data
            print_result(success, f"executionTime={data.get('executionTime', '?')}s")
            results["passed" if success else "failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            results["failed"] += 1

        # ------------------------------------------------------------------
        # Test 8: TSDB Status (cardinality)
        # ------------------------------------------------------------------
        print_test("Test 8: TSDB Status (Cardinality)")
        results["total"] += 1
        try:
            r = await client.post(
                f"{base_url}/tools/tsdb_status",
                json={"arguments": {"topN": 5}},
            )
            data = r.json()
            success = data.get("success", False) or "result" in data
            print_result(success, f"executionTime={data.get('executionTime', '?')}s")
            results["passed" if success else "failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            results["failed"] += 1

        # ------------------------------------------------------------------
        # Test 9: Alerts
        # ------------------------------------------------------------------
        print_test("Test 9: Alerts (firing/pending)")
        results["total"] += 1
        try:
            r = await client.post(
                f"{base_url}/tools/alerts",
                json={"arguments": {}},
            )
            data = r.json()
            success = data.get("success", False) or "result" in data
            print_result(success, f"executionTime={data.get('executionTime', '?')}s")
            results["passed" if success else "failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            results["failed"] += 1

        # ------------------------------------------------------------------
        # Test 10: Range query
        # ------------------------------------------------------------------
        print_test("Test 10: Range Query (up, last 5m)")
        results["total"] += 1
        try:
            import time
            now = int(time.time())
            start = now - 300  # 5 minutes ago
            r = await client.post(
                f"{base_url}/tools/query_range",
                json={"arguments": {
                    "query": "up",
                    "start": str(start),
                    "end": str(now),
                    "step": "60",
                }},
            )
            data = r.json()
            success = data.get("success", False) or "result" in data
            print_result(success, f"executionTime={data.get('executionTime', '?')}s")
            results["passed" if success else "failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            results["failed"] += 1

        # ------------------------------------------------------------------
        # Test 11: Documentation search
        # ------------------------------------------------------------------
        print_test("Test 11: Documentation Search (MetricsQL)")
        results["total"] += 1
        try:
            r = await client.post(
                f"{base_url}/tools/documentation",
                json={"arguments": {"query": "MetricsQL"}},
            )
            data = r.json()
            success = data.get("success", False) or "result" in data
            print_result(success, f"executionTime={data.get('executionTime', '?')}s")
            results["passed" if success else "failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            results["failed"] += 1

        # ------------------------------------------------------------------
        # Test 12: Unknown tool (should return error)
        # ------------------------------------------------------------------
        print_test("Test 12: Unknown Tool (should fail gracefully)")
        results["total"] += 1
        try:
            r = await client.post(
                f"{base_url}/tools/nonexistent_tool",
                json={"arguments": {}},
            )
            data = r.json()
            success = data.get("success") is False or "error" in data
            print_result(success, f"Got expected error: {data.get('error', '?')[:80]}")
            results["passed" if success else "failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            results["failed"] += 1

    finally:
        await client.aclose()

    # Summary
    print_header("Test Summary")
    print(f"Total Tests:  {results['total']}")
    print(f"Passed:       {results['passed']} ✓")
    print(f"Failed:       {results['failed']} ✗")
    rate = (results["passed"] / results["total"] * 100) if results["total"] > 0 else 0
    print(f"Success Rate: {rate:.1f}%")

    if results["failed"] == 0:
        print("\n🎉 All tests passed!")
    else:
        print(f"\n⚠️  {results['failed']} test(s) failed")

    return results["failed"] == 0


if __name__ == "__main__":
    url = "http://localhost:8084"
    for arg in sys.argv[1:]:
        if not arg.startswith("-"):
            url = arg
            break

    print(f"Mode: REST")
    print(f"URL: {url}")
    print(f"Verbose: {VERBOSE}\n")

    success = asyncio.run(test_vm_mcp_proxy(url))
    sys.exit(0 if success else 1)
