#!/usr/bin/env python
"""Test the Grafana MCP server directly"""
import asyncio
import json
import os

# Set test environment variables
os.environ["GRAFANA_URL"] = "http://127.0.0.1:3000/"
os.environ["GRAFANA_TOKEN"] = os.environ.get("GRAFANA_TOKEN", "")
os.environ["GRAFANA_ORG_ID"] = "1"
os.environ["GRAFANA_TIMEOUT_S"] = "15"

from grafana import server, call_tool


async def test_find_firing_alerts():
    """Test find_firing_alerts with empty arguments"""
    print("Testing find_firing_alerts with empty dict...")
    result = await call_tool("find_firing_alerts", {})
    print(f"Result: {json.dumps(result.model_dump(), indent=2)}")


async def test_find_firing_alerts_with_labels():
    """Test find_firing_alerts with label filter"""
    print("\nTesting find_firing_alerts with labels...")
    result = await call_tool("find_firing_alerts", {"labels": {"service": "api-gateway"}})
    print(f"Result: {json.dumps(result.model_dump(), indent=2)}")


async def main():
    try:
        await test_find_firing_alerts()
        await test_find_firing_alerts_with_labels()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
