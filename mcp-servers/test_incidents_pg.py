#!/usr/bin/env python3
"""
Test the Incidents PG MCP server.
Requires PG_HOST, PG_USER, PG_PASSWORD, PG_DATABASE env vars.
Can test via REST (SSE mode) or direct function call.
"""
import asyncio
import json
import os
import sys

# For direct testing (not via REST)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def test_via_rest(base_url: str = "http://localhost:8082"):
    """Test via REST endpoints (requires SSE mode running)."""
    import httpx

    client = httpx.AsyncClient(timeout=15)

    print("=" * 60)
    print("Testing Incidents PG MCP via REST")
    print(f"Base URL: {base_url}")
    print("=" * 60)

    # Health
    print("\n1. Health check...")
    r = await client.get(f"{base_url}/health")
    print(f"   Status: {r.status_code} — {r.json()}")

    # List tools
    print("\n2. List tools...")
    r = await client.get(f"{base_url}/tools")
    tools = r.json().get("tools", [])
    for t in tools:
        print(f"   - {t['name']}: {t['description'][:60]}")

    # Search incidents (all)
    print("\n3. Search incidents (no filter, limit 5)...")
    r = await client.post(
        f"{base_url}/tools/search_incidents",
        json={"arguments": {"limit": 5}},
    )
    data = r.json()
    print(f"   Success: {data.get('success')}, Count: {data.get('count')}")
    for inc in data.get("result", [])[:3]:
        print(f"   - {inc.get('number')}: {inc.get('short_description', '')[:50]}")

    # Get incident by number (use first from search)
    incidents = data.get("result", [])
    if incidents:
        number = incidents[0].get("number")
        print(f"\n4. Get incident {number}...")
        r = await client.post(
            f"{base_url}/tools/get_incident",
            json={"arguments": {"number": number}},
        )
        inc = r.json()
        print(f"   Success: {inc.get('success')}")
        if inc.get("success"):
            result = inc["result"]
            print(f"   number: {result.get('number')}")
            print(f"   cmdb_ci_name: {result.get('cmdb_ci_name')}")
            print(f"   priority: {result.get('priority')}")
            print(f"   state: {result.get('state')}")
            gl = result.get("_grafana_labels", {})
            if gl:
                print(f"   _grafana_labels: {len(gl)} labels parsed from description")
            parsed = result.get("_parsed", {})
            if parsed.get("alert_rule_uid"):
                print(f"   alert_rule_uid: {parsed['alert_rule_uid']}")

    # Stats
    print("\n5. Incident stats (last 30 days, by priority)...")
    r = await client.post(
        f"{base_url}/tools/get_incident_stats",
        json={"arguments": {"days": 30, "group_by": "priority"}},
    )
    stats = r.json()
    print(f"   Total: {stats.get('total')}")
    for row in stats.get("result", []):
        print(f"   - {row['key']}: {row['count']}")

    await client.aclose()
    print("\n" + "=" * 60)
    print("All REST tests completed.")


async def test_direct():
    """Test by calling functions directly (requires PG env vars)."""
    from incidents_pg import call_tool

    print("=" * 60)
    print("Testing Incidents PG MCP — Direct function calls")
    print("=" * 60)

    # Search
    print("\n1. search_incidents (limit 3)...")
    result = await call_tool("search_incidents", {"limit": 3})
    data = json.loads(result[0]["text"])
    print(f"   Success: {data.get('success')}, Count: {data.get('count')}")

    # Get first incident
    incidents = data.get("result", [])
    if incidents:
        number = incidents[0].get("number")
        print(f"\n2. get_incident({number})...")
        result = await call_tool("get_incident", {"number": number})
        inc = json.loads(result[0]["text"])
        print(f"   Success: {inc.get('success')}")

    # Stats
    print("\n3. get_incident_stats (30 days, by priority)...")
    result = await call_tool("get_incident_stats", {"days": 30, "group_by": "priority"})
    stats = json.loads(result[0]["text"])
    print(f"   Total: {stats.get('total')}")

    print("\nDirect tests completed.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "rest"
    url = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8082"

    if mode == "direct":
        asyncio.run(test_direct())
    else:
        asyncio.run(test_via_rest(url))
