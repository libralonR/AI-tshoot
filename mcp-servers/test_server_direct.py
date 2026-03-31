#!/usr/bin/env python
"""Test the Grafana MCP server directly."""
import asyncio
import json
import os

# Set test environment variables
os.environ["GRAFANA_URL"] = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000/")
os.environ["GRAFANA_TOKEN"] = os.environ.get("GRAFANA_TOKEN", "")
os.environ["GRAFANA_ORG_ID"] = os.environ.get("GRAFANA_ORG_ID", "1")
os.environ["GRAFANA_TIMEOUT_S"] = "15"

from grafana_v2 import call_tool


async def test_find_firing_alerts():
    """Test find_firing_alerts with empty arguments."""
    print("Testing find_firing_alerts with empty dict...")
    result = await call_tool("find_firing_alerts", {})
    for item in result:
        print(json.dumps(json.loads(item["text"]), indent=2))


async def test_find_firing_alerts_with_labels():
    """Test find_firing_alerts with label filter."""
    print("\nTesting find_firing_alerts with labels...")
    result = await call_tool("find_firing_alerts", {"application_service": "alessandra_app"})
    for item in result:
        print(json.dumps(json.loads(item["text"]), indent=2))


async def test_find_dashboards():
    """Test find_dashboards."""
    print("\nTesting find_dashboards...")
    result = await call_tool("find_dashboards", {})
    for item in result:
        print(json.dumps(json.loads(item["text"]), indent=2))


async def main():
    try:
        await test_find_firing_alerts()
        await test_find_firing_alerts_with_labels()
        await test_find_dashboards()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
