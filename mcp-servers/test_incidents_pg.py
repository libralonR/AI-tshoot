#!/usr/bin/env python3
"""
Test the Incidents PG MCP server.
Requires PG_HOST, PG_USER, PG_PASSWORD, PG_DATABASE env vars.
Can test via REST (SSE mode) or direct function call.

Usage:
    # Test via REST (requires server running)
    python test_incidents_pg.py rest http://localhost:8082
    
    # Test via direct function calls
    python test_incidents_pg.py direct
    
    # Run all tests with verbose output
    python test_incidents_pg.py rest http://localhost:8082 --verbose
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, Any, List

# For direct testing (not via REST)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Test configuration
VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv


def print_header(title: str):
    """Print a formatted test section header."""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_test(test_name: str):
    """Print a test name."""
    print(f"\n▶ {test_name}")


def print_result(success: bool, message: str = ""):
    """Print test result."""
    status = "✓ PASS" if success else "✗ FAIL"
    print(f"  {status}", end="")
    if message:
        print(f" - {message}")
    else:
        print()


def print_detail(key: str, value: Any):
    """Print a detail line."""
    if VERBOSE or key in ["number", "cmdb_ci_name", "priority", "state", "count", "total"]:
        print(f"    {key}: {value}")


def print_json(data: Dict[str, Any], indent: int = 4):
    """Print JSON data if verbose."""
    if VERBOSE:
        print(json.dumps(data, indent=indent, default=str))


async def test_via_rest(base_url: str = "http://localhost:8082"):
    """Test via REST endpoints (requires SSE mode running)."""
    import httpx

    client = httpx.AsyncClient(timeout=30, verify=False)
    test_results = {"passed": 0, "failed": 0, "total": 0}

    print_header("Testing Incidents PG MCP via REST")
    print(f"Base URL: {base_url}")

    try:
        # Test 1: Health check
        print_test("Test 1: Health Check")
        test_results["total"] += 1
        try:
            r = await client.get(f"{base_url}/health")
            success = r.status_code == 200 and r.json().get("status") == "ok"
            print_result(success, f"Status: {r.status_code}")
            if success:
                test_results["passed"] += 1
            else:
                test_results["failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            test_results["failed"] += 1

        # Test 2: List tools
        print_test("Test 2: List Available Tools")
        test_results["total"] += 1
        try:
            r = await client.get(f"{base_url}/tools")
            tools = r.json().get("tools", [])
            success = len(tools) >= 4  # Expecting at least 4 tools
            print_result(success, f"Found {len(tools)} tools")
            for t in tools:
                print_detail("tool", f"{t['name']}: {t['description'][:60]}...")
            if success:
                test_results["passed"] += 1
            else:
                test_results["failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            test_results["failed"] += 1

        # Test 3: Search incidents (no filter)
        print_test("Test 3: Search Incidents (No Filter, Limit 5)")
        test_results["total"] += 1
        try:
            r = await client.post(
                f"{base_url}/tools/search_incidents",
                json={"arguments": {"limit": 5}},
            )
            data = r.json()
            success = data.get("success") and data.get("count") >= 0
            print_result(success, f"Count: {data.get('count')}")
            print_detail("executionTime", f"{data.get('executionTime', 0):.3f}s")
            
            incidents = data.get("result", [])
            for i, inc in enumerate(incidents[:3], 1):
                print_detail(f"incident_{i}", 
                           f"{inc.get('number')} | {inc.get('cmdb_ci_name')} | "
                           f"P{inc.get('priority')} | {inc.get('state')}")
            
            if success:
                test_results["passed"] += 1
            else:
                test_results["failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            test_results["failed"] += 1

        # Test 4: Get incident by number
        incidents = data.get("result", []) if 'data' in locals() else []
        if incidents:
            number = incidents[0].get("number")
            print_test(f"Test 4: Get Incident by Number ({number})")
            test_results["total"] += 1
            try:
                r = await client.post(
                    f"{base_url}/tools/get_incident",
                    json={"arguments": {"number": number}},
                )
                inc = r.json()
                success = inc.get("success")
                print_result(success)
                
                if success:
                    result = inc["result"]
                    print_detail("number", result.get("number"))
                    print_detail("cmdb_ci_name", result.get("cmdb_ci_name"))
                    print_detail("priority", result.get("priority"))
                    print_detail("state", result.get("state"))
                    print_detail("assignment_group", result.get("assignment_group_name"))
                    
                    gl = result.get("_grafana_labels", {})
                    if gl:
                        print_detail("grafana_labels_count", len(gl))
                        if VERBOSE:
                            for k, v in list(gl.items())[:5]:
                                print_detail(f"  label_{k}", v)
                    
                    parsed = result.get("_parsed", {})
                    if parsed.get("alert_rule_uid"):
                        print_detail("alert_rule_uid", parsed["alert_rule_uid"])
                    
                    print_detail("executionTime", f"{inc.get('executionTime', 0):.3f}s")
                    test_results["passed"] += 1
                else:
                    test_results["failed"] += 1
            except Exception as e:
                print_result(False, f"Error: {e}")
                test_results["failed"] += 1
        else:
            print_test("Test 4: Get Incident by Number (SKIPPED - no incidents found)")

        # Test 5: Search by application_service
        print_test("Test 5: Search by Application Service")
        test_results["total"] += 1
        try:
            # Use cmdb_ci_name from first incident if available
            app_svc = incidents[0].get("cmdb_ci_name") if incidents else "test-service"
            r = await client.post(
                f"{base_url}/tools/search_incidents",
                json={"arguments": {"application_service": app_svc, "limit": 10}},
            )
            data = r.json()
            success = data.get("success")
            print_result(success, f"Filter: {app_svc} | Count: {data.get('count')}")
            print_detail("executionTime", f"{data.get('executionTime', 0):.3f}s")
            
            if success:
                test_results["passed"] += 1
            else:
                test_results["failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            test_results["failed"] += 1

        # Test 6: Search by priority
        print_test("Test 6: Search by Priority (P1)")
        test_results["total"] += 1
        try:
            r = await client.post(
                f"{base_url}/tools/search_incidents",
                json={"arguments": {"priority": "1", "limit": 10}},
            )
            data = r.json()
            success = data.get("success")
            print_result(success, f"Count: {data.get('count')}")
            print_detail("executionTime", f"{data.get('executionTime', 0):.3f}s")
            
            if success:
                test_results["passed"] += 1
            else:
                test_results["failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            test_results["failed"] += 1

        # Test 7: Search by date range
        print_test("Test 7: Search by Date Range (Last 7 Days)")
        test_results["total"] += 1
        try:
            now = datetime.utcnow()
            week_ago = now - timedelta(days=7)
            r = await client.post(
                f"{base_url}/tools/search_incidents",
                json={
                    "arguments": {
                        "opened_after": week_ago.isoformat(),
                        "opened_before": now.isoformat(),
                        "limit": 20
                    }
                },
            )
            data = r.json()
            success = data.get("success")
            print_result(success, f"Count: {data.get('count')}")
            print_detail("date_range", f"{week_ago.date()} to {now.date()}")
            print_detail("executionTime", f"{data.get('executionTime', 0):.3f}s")
            
            if success:
                test_results["passed"] += 1
            else:
                test_results["failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            test_results["failed"] += 1

        # Test 8: Get related incidents by number
        if incidents and incidents[0].get("number"):
            number = incidents[0].get("number")
            print_test(f"Test 8: Get Related Incidents by Number ({number})")
            test_results["total"] += 1
            try:
                r = await client.post(
                    f"{base_url}/tools/get_related_incidents",
                    json={"arguments": {"number": number, "time_window_hours": 48}},
                )
                data = r.json()
                success = data.get("success")
                result = data.get("result", {})
                by_parent = len(result.get("by_parent", []))
                by_ci = len(result.get("by_ci", []))
                print_result(success, f"by_parent={by_parent}, by_ci={by_ci}")
                print_detail("executionTime", f"{data.get('executionTime', 0):.3f}s")
                
                if success:
                    test_results["passed"] += 1
                else:
                    test_results["failed"] += 1
            except Exception as e:
                print_result(False, f"Error: {e}")
                test_results["failed"] += 1
        else:
            print_test("Test 8: Get Related Incidents (SKIPPED - no incidents found)")

        # Test 9: Get related incidents by application_service
        if incidents and incidents[0].get("cmdb_ci_name"):
            app_svc = incidents[0].get("cmdb_ci_name")
            print_test(f"Test 9: Get Related Incidents by Service ({app_svc})")
            test_results["total"] += 1
            try:
                r = await client.post(
                    f"{base_url}/tools/get_related_incidents",
                    json={"arguments": {"application_service": app_svc}},
                )
                data = r.json()
                success = data.get("success")
                count = data.get("count", 0)
                print_result(success, f"Count: {count}")
                print_detail("executionTime", f"{data.get('executionTime', 0):.3f}s")
                
                if success:
                    test_results["passed"] += 1
                else:
                    test_results["failed"] += 1
            except Exception as e:
                print_result(False, f"Error: {e}")
                test_results["failed"] += 1
        else:
            print_test("Test 9: Get Related Incidents by Service (SKIPPED)")

        # Test 10: Get incident stats (by priority)
        print_test("Test 10: Get Incident Stats (Last 30 Days, by Priority)")
        test_results["total"] += 1
        try:
            r = await client.post(
                f"{base_url}/tools/get_incident_stats",
                json={"arguments": {"days": 30, "group_by": "priority"}},
            )
            stats = r.json()
            success = stats.get("success")
            print_result(success, f"Total: {stats.get('total')}")
            print_detail("period", f"{stats.get('period_days')} days")
            print_detail("executionTime", f"{stats.get('executionTime', 0):.3f}s")
            
            for row in stats.get("result", [])[:5]:
                print_detail(f"priority_{row['key']}", f"{row['count']} incidents")
            
            if success:
                test_results["passed"] += 1
            else:
                test_results["failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            test_results["failed"] += 1

        # Test 11: Get incident stats (by state)
        print_test("Test 11: Get Incident Stats (Last 30 Days, by State)")
        test_results["total"] += 1
        try:
            r = await client.post(
                f"{base_url}/tools/get_incident_stats",
                json={"arguments": {"days": 30, "group_by": "state"}},
            )
            stats = r.json()
            success = stats.get("success")
            print_result(success, f"Total: {stats.get('total')}")
            
            for row in stats.get("result", [])[:5]:
                print_detail(f"state_{row['key']}", f"{row['count']} incidents")
            
            if success:
                test_results["passed"] += 1
            else:
                test_results["failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            test_results["failed"] += 1

        # Test 12: Get incident stats with filter
        if incidents and incidents[0].get("cmdb_ci_name"):
            app_svc = incidents[0].get("cmdb_ci_name")
            print_test(f"Test 12: Get Incident Stats for Service ({app_svc})")
            test_results["total"] += 1
            try:
                r = await client.post(
                    f"{base_url}/tools/get_incident_stats",
                    json={
                        "arguments": {
                            "application_service": app_svc,
                            "days": 60,
                            "group_by": "priority"
                        }
                    },
                )
                stats = r.json()
                success = stats.get("success")
                print_result(success, f"Total: {stats.get('total')}")
                print_detail("service", app_svc)
                
                if success:
                    test_results["passed"] += 1
                else:
                    test_results["failed"] += 1
            except Exception as e:
                print_result(False, f"Error: {e}")
                test_results["failed"] += 1
        else:
            print_test("Test 12: Get Incident Stats for Service (SKIPPED)")

    finally:
        await client.aclose()

    # Print summary
    print_header("Test Summary")
    print(f"Total Tests:  {test_results['total']}")
    print(f"Passed:       {test_results['passed']} ✓")
    print(f"Failed:       {test_results['failed']} ✗")
    success_rate = (test_results['passed'] / test_results['total'] * 100) if test_results['total'] > 0 else 0
    print(f"Success Rate: {success_rate:.1f}%")
    
    if test_results['failed'] == 0:
        print("\n🎉 All tests passed!")
    else:
        print(f"\n⚠️  {test_results['failed']} test(s) failed")
    
    return test_results['failed'] == 0


async def test_direct():
    """Test by calling functions directly (requires PG env vars)."""
    from incidents_pg import call_tool

    test_results = {"passed": 0, "failed": 0, "total": 0}

    print_header("Testing Incidents PG MCP — Direct Function Calls")

    # Test 1: Search incidents
    print_test("Test 1: search_incidents (limit 5)")
    test_results["total"] += 1
    try:
        result = await call_tool("search_incidents", {"limit": 5})
        data = json.loads(result[0]["text"])
        success = data.get("success")
        print_result(success, f"Count: {data.get('count')}")
        print_detail("executionTime", f"{data.get('executionTime', 0):.3f}s")
        
        incidents = data.get("result", [])
        for i, inc in enumerate(incidents[:3], 1):
            print_detail(f"incident_{i}", 
                       f"{inc.get('number')} | {inc.get('cmdb_ci_name')} | P{inc.get('priority')}")
        
        if success:
            test_results["passed"] += 1
        else:
            test_results["failed"] += 1
    except Exception as e:
        print_result(False, f"Error: {e}")
        test_results["failed"] += 1

    # Test 2: Get first incident
    incidents = data.get("result", []) if 'data' in locals() else []
    if incidents:
        number = incidents[0].get("number")
        print_test(f"Test 2: get_incident({number})")
        test_results["total"] += 1
        try:
            result = await call_tool("get_incident", {"number": number})
            inc = json.loads(result[0]["text"])
            success = inc.get("success")
            print_result(success)
            
            if success:
                result_data = inc["result"]
                print_detail("cmdb_ci_name", result_data.get("cmdb_ci_name"))
                print_detail("priority", result_data.get("priority"))
                print_detail("state", result_data.get("state"))
                
                gl = result_data.get("_grafana_labels", {})
                if gl:
                    print_detail("grafana_labels_count", len(gl))
                
                test_results["passed"] += 1
            else:
                test_results["failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            test_results["failed"] += 1
    else:
        print_test("Test 2: get_incident (SKIPPED - no incidents found)")

    # Test 3: Search by application_service
    if incidents and incidents[0].get("cmdb_ci_name"):
        app_svc = incidents[0].get("cmdb_ci_name")
        print_test(f"Test 3: search_incidents(application_service={app_svc})")
        test_results["total"] += 1
        try:
            result = await call_tool("search_incidents", {
                "application_service": app_svc,
                "limit": 10
            })
            data = json.loads(result[0]["text"])
            success = data.get("success")
            print_result(success, f"Count: {data.get('count')}")
            
            if success:
                test_results["passed"] += 1
            else:
                test_results["failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            test_results["failed"] += 1
    else:
        print_test("Test 3: search_incidents by service (SKIPPED)")

    # Test 4: Get related incidents
    if incidents and incidents[0].get("number"):
        number = incidents[0].get("number")
        print_test(f"Test 4: get_related_incidents(number={number})")
        test_results["total"] += 1
        try:
            result = await call_tool("get_related_incidents", {
                "number": number,
                "time_window_hours": 48
            })
            data = json.loads(result[0]["text"])
            success = data.get("success")
            result_data = data.get("result", {})
            by_parent = len(result_data.get("by_parent", []))
            by_ci = len(result_data.get("by_ci", []))
            print_result(success, f"by_parent={by_parent}, by_ci={by_ci}")
            
            if success:
                test_results["passed"] += 1
            else:
                test_results["failed"] += 1
        except Exception as e:
            print_result(False, f"Error: {e}")
            test_results["failed"] += 1
    else:
        print_test("Test 4: get_related_incidents (SKIPPED)")

    # Test 5: Get incident stats
    print_test("Test 5: get_incident_stats (30 days, by priority)")
    test_results["total"] += 1
    try:
        result = await call_tool("get_incident_stats", {
            "days": 30,
            "group_by": "priority"
        })
        stats = json.loads(result[0]["text"])
        success = stats.get("success")
        print_result(success, f"Total: {stats.get('total')}")
        print_detail("executionTime", f"{stats.get('executionTime', 0):.3f}s")
        
        for row in stats.get("result", [])[:5]:
            print_detail(f"priority_{row['key']}", f"{row['count']} incidents")
        
        if success:
            test_results["passed"] += 1
        else:
            test_results["failed"] += 1
    except Exception as e:
        print_result(False, f"Error: {e}")
        test_results["failed"] += 1

    # Test 6: Get incident stats by state
    print_test("Test 6: get_incident_stats (30 days, by state)")
    test_results["total"] += 1
    try:
        result = await call_tool("get_incident_stats", {
            "days": 30,
            "group_by": "state"
        })
        stats = json.loads(result[0]["text"])
        success = stats.get("success")
        print_result(success, f"Total: {stats.get('total')}")
        
        for row in stats.get("result", [])[:5]:
            print_detail(f"state_{row['key']}", f"{row['count']} incidents")
        
        if success:
            test_results["passed"] += 1
        else:
            test_results["failed"] += 1
    except Exception as e:
        print_result(False, f"Error: {e}")
        test_results["failed"] += 1

    # Print summary
    print_header("Test Summary")
    print(f"Total Tests:  {test_results['total']}")
    print(f"Passed:       {test_results['passed']} ✓")
    print(f"Failed:       {test_results['failed']} ✗")
    success_rate = (test_results['passed'] / test_results['total'] * 100) if test_results['total'] > 0 else 0
    print(f"Success Rate: {success_rate:.1f}%")
    
    if test_results['failed'] == 0:
        print("\n🎉 All tests passed!")
    else:
        print(f"\n⚠️  {test_results['failed']} test(s) failed")
    
    return test_results['failed'] == 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] not in ["--verbose", "-v"] else "rest"
    url = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else "http://localhost:8082"

    print(f"\nMode: {mode}")
    print(f"Verbose: {VERBOSE}\n")

    if mode == "direct":
        success = asyncio.run(test_direct())
    else:
        success = asyncio.run(test_via_rest(url))
    
    sys.exit(0 if success else 1)
