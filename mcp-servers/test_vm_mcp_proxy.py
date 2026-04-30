#!/usr/bin/env python3
"""
Test the VM MCP Proxy adapter.

Usage:
    python test_vm_mcp_proxy.py http://localhost:8084
    python test_vm_mcp_proxy.py http://localhost:8084 --verbose

    # Testar direto contra o VM MCP (sem proxy) para diagnosticar
    python test_vm_mcp_proxy.py http://localhost:8083 --direct-sse
    python test_vm_mcp_proxy.py http://localhost:8083 --direct-http
"""
import asyncio
import json
import sys
import time
from typing import Any

import httpx

VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv
DIRECT_SSE = "--direct-sse" in sys.argv
DIRECT_HTTP = "--direct-http" in sys.argv


def pheader(title: str):
    print(f"\n{'=' * 70}\n  {title}\n{'=' * 70}")

def ptest(name: str):
    print(f"\n▶ {name}")

def ppass(msg: str = ""):
    print(f"  ✓ PASS{f' - {msg}' if msg else ''}")

def pfail(msg: str = ""):
    print(f"  ✗ FAIL{f' - {msg}' if msg else ''}")

def pdetail(key: str, val: Any):
    if VERBOSE:
        print(f"    {key}: {val}")


# ---------------------------------------------------------------------------
# Test via REST proxy (/tools/{name})
# ---------------------------------------------------------------------------
async def test_via_proxy(base_url: str):
    r = {"passed": 0, "failed": 0, "total": 0}
    client = httpx.AsyncClient(timeout=60, verify=False)

    pheader(f"Testing VM MCP Proxy — {base_url}")

    try:
        # 1. Health
        ptest("1. Health Check")
        r["total"] += 1
        try:
            resp = await client.get(f"{base_url}/health")
            data = resp.json()
            ok = resp.status_code == 200 and data.get("upstream_healthy")
            (ppass if ok else pfail)(f"status={data.get('status')} upstream_healthy={data.get('upstream_healthy')}")
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

        # 2. List tools
        ptest("2. List Tools")
        r["total"] += 1
        try:
            resp = await client.get(f"{base_url}/tools")
            data = resp.json()
            tools = data.get("tools", [])
            ok = len(tools) >= 5
            (ppass if ok else pfail)(f"{len(tools)} tools found")
            for t in tools[:5]:
                pdetail("tool", t.get("name"))
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

        # 3. Simple query: up
        ptest("3. Query: up")
        r["total"] += 1
        try:
            resp = await client.post(f"{base_url}/tools/query", json={"arguments": {"query": "up", "step": "5m"}})
            data = resp.json()
            ok = resp.status_code == 200 and (data.get("success", False) or "result" in data or "status" in data)
            (ppass if ok else pfail)(f"status={resp.status_code} time={data.get('executionTime', '?')}s")
            if VERBOSE:
                result_str = json.dumps(data, default=str)
                pdetail("result_size", f"{len(result_str)} bytes")
                pdetail("result_preview", result_str[:300])
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

        # 4. Query: count of series
        ptest("4. Query: count({__name__=~'.+'})")
        r["total"] += 1
        try:
            resp = await client.post(f"{base_url}/tools/query", json={"arguments": {"query": "count({__name__=~'.+'})"}})
            data = resp.json()
            ok = resp.status_code == 200
            (ppass if ok else pfail)(f"status={resp.status_code}")
            if VERBOSE:
                pdetail("result_preview", json.dumps(data, default=str)[:300])
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

        # 5. Labels
        ptest("5. Labels")
        r["total"] += 1
        try:
            resp = await client.post(f"{base_url}/tools/labels", json={"arguments": {}})
            data = resp.json()
            ok = resp.status_code == 200
            (ppass if ok else pfail)(f"status={resp.status_code}")
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

        # 6. TSDB Status
        ptest("6. TSDB Status")
        r["total"] += 1
        try:
            resp = await client.post(f"{base_url}/tools/tsdb_status", json={"arguments": {"topN": 3}})
            data = resp.json()
            ok = resp.status_code == 200
            (ppass if ok else pfail)(f"status={resp.status_code} time={data.get('executionTime', '?')}s")
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

        # 7. Range query
        ptest("7. Range Query: up (last 5m)")
        r["total"] += 1
        try:
            now = int(time.time())
            resp = await client.post(f"{base_url}/tools/query_range", json={
                "arguments": {"query": "up", "start": str(now - 300), "end": str(now), "step": "60"}
            })
            data = resp.json()
            ok = resp.status_code == 200
            (ppass if ok else pfail)(f"status={resp.status_code}")
            r["passed" if ok else "failed"] += 1
        except Exception as e:
            pfail(str(e)); r["failed"] += 1

        # 8. Unknown tool
        ptest("8. Unknown Tool (should fail gracefully)")
        r["total"] += 1
        try:
            resp = await client.post(f"{base_url}/tools/nonexistent", json={"arguments": {}})
            data = resp.json()
            ok = data.get("success") is False or "error" in data
            (ppass if ok else pfail)(f"error={data.get('error', '?')[:80]}")
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


# ---------------------------------------------------------------------------
# Test direto contra VM MCP via SSE (sem proxy — para diagnosticar)
# ---------------------------------------------------------------------------
async def test_direct_sse(upstream: str):
    """Testa o protocolo MCP SSE diretamente contra o VM MCP Go binary."""
    import uuid

    pheader(f"Direct SSE Test — {upstream}")

    ptest("1. GET /sse (obter session URL)")
    message_url = None
    try:
        async with httpx.AsyncClient(verify=False, timeout=httpx.Timeout(10)) as http:
            async with http.stream("GET", f"{upstream}/sse") as resp:
                print(f"  Status: {resp.status_code}")
                print(f"  Headers: {dict(resp.headers)}")
                async for line in resp.aiter_lines():
                    line = line.strip()
                    print(f"  SSE line: {line}")
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data.startswith("/"):
                            message_url = f"{upstream}{data}"
                        else:
                            message_url = data
                        print(f"  → message_url: {message_url}")
                        break
    except Exception as e:
        pfail(f"SSE connection failed: {e}")
        return False

    if not message_url:
        pfail("No message URL received from SSE")
        return False
    ppass(f"message_url={message_url}")

    ptest("2. POST /message (initialize) — ENQUANTO SSE ESTÁ ABERTO")
    print("  Nota: O SSE stream já foi fechado. Vamos ver se o session ID ainda é válido...")

    msg_id = str(uuid.uuid4())
    msg = {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "direct-test", "version": "1.0.0"},
        },
    }

    try:
        async with httpx.AsyncClient(verify=False, timeout=10) as http:
            resp = await http.post(message_url, json=msg, headers={"Content-Type": "application/json"})
            print(f"  Status: {resp.status_code}")
            print(f"  Body: {resp.text[:300]}")
            if resp.status_code == 400:
                pfail("400 Bad Request — confirma que o session ID expira quando o SSE stream fecha")
                print("\n  DIAGNÓSTICO: O VM MCP em modo SSE requer que o stream SSE fique ABERTO")
                print("  durante toda a sessão. Quando o stream fecha, o session ID é invalidado.")
                print("  O proxy v5 resolve isso mantendo o stream aberto durante cada operação.")
            elif resp.status_code in (200, 202):
                ppass(f"Initialize aceito! status={resp.status_code}")
            else:
                pfail(f"Unexpected status: {resp.status_code}")
    except Exception as e:
        pfail(f"POST failed: {e}")

    ptest("3. POST /message (initialize) — COM SSE ABERTO em paralelo")
    print("  Abrindo SSE stream e enviando initialize simultaneamente...")

    try:
        http_client = httpx.AsyncClient(verify=False, timeout=httpx.Timeout(None))
        message_url2 = None
        connected = asyncio.Event()
        response_received = asyncio.Event()
        init_response = {}

        async def reader():
            nonlocal message_url2
            try:
                async with http_client.stream("GET", f"{upstream}/sse") as resp:
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if line.startswith("data:") and message_url2 is None:
                            data = line[5:].strip()
                            message_url2 = f"{upstream}{data}" if data.startswith("/") else data
                            connected.set()
                        elif line.startswith("data:") and message_url2:
                            try:
                                msg_data = json.loads(line[5:].strip())
                                if msg_data.get("id") == msg_id:
                                    init_response.update(msg_data)
                                    response_received.set()
                            except json.JSONDecodeError:
                                pass
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(reader())
        await asyncio.wait_for(connected.wait(), timeout=10)
        print(f"  SSE connected: {message_url2}")

        msg_id2 = str(uuid.uuid4())
        init_msg = {
            "jsonrpc": "2.0", "id": msg_id2, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "direct-test-parallel", "version": "1.0.0"}},
        }

        async with httpx.AsyncClient(verify=False, timeout=10) as post_http:
            resp = await post_http.post(message_url2, json=init_msg, headers={"Content-Type": "application/json"})
            print(f"  POST status: {resp.status_code}")
            print(f"  POST body: {resp.text[:200]}")

            if resp.status_code == 202:
                print("  Waiting for response via SSE stream...")
                try:
                    await asyncio.wait_for(response_received.wait(), timeout=5)
                    print(f"  SSE response: {json.dumps(init_response)[:200]}")
                    ppass("Initialize works with SSE stream open!")
                except asyncio.TimeoutError:
                    print("  No SSE response received (may have been in POST body)")
                    ppass(f"POST returned {resp.status_code}")
            elif resp.status_code == 200:
                ppass(f"Initialize returned directly: {resp.text[:200]}")
            elif resp.status_code == 400:
                pfail(f"Still getting 400 even with SSE open!")
            else:
                print(f"  Unexpected: {resp.status_code}")

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        await http_client.aclose()

    except Exception as e:
        pfail(f"Parallel test failed: {e}")

    return True


# ---------------------------------------------------------------------------
# Test direto contra VM MCP via Streamable HTTP (sem proxy)
# ---------------------------------------------------------------------------
async def test_direct_http(upstream: str):
    """Testa o protocolo MCP Streamable HTTP diretamente."""
    import uuid

    pheader(f"Direct Streamable HTTP Test — {upstream}/mcp")

    ptest("1. POST /mcp (initialize)")
    session_id = None
    try:
        msg = {
            "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "direct-http-test", "version": "1.0.0"}},
        }
        async with httpx.AsyncClient(verify=False, timeout=10) as http:
            resp = await http.post(
                f"{upstream}/mcp", json=msg,
                headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            )
            print(f"  Status: {resp.status_code}")
            print(f"  Content-Type: {resp.headers.get('content-type')}")
            print(f"  Mcp-Session-Id: {resp.headers.get('mcp-session-id', 'NONE')}")
            print(f"  Body: {resp.text[:300]}")
            session_id = resp.headers.get("mcp-session-id")

            if resp.status_code in (200, 202):
                ppass(f"Initialize OK | session={session_id}")
            elif resp.status_code == 404:
                pfail("/mcp not found — server is probably in SSE mode, not HTTP mode")
                return False
            else:
                pfail(f"Unexpected: {resp.status_code}")
                return False
    except Exception as e:
        pfail(f"Failed: {e}")
        return False

    if not session_id:
        print("  ⚠️  No session ID returned — server may be stateless")

    ptest("2. POST /mcp (notifications/initialized)")
    try:
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        headers = {"Content-Type": "application/json"}
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        async with httpx.AsyncClient(verify=False, timeout=10) as http:
            resp = await http.post(f"{upstream}/mcp", json=notif, headers=headers)
            print(f"  Status: {resp.status_code}")
            ppass(f"Notification sent")
    except Exception as e:
        pfail(f"Failed: {e}")

    ptest("3. POST /mcp (tools/call — query 'up')")
    try:
        msg = {
            "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tools/call",
            "params": {"name": "query", "arguments": {"query": "up"}},
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        async with httpx.AsyncClient(verify=False, timeout=30) as http:
            resp = await http.post(f"{upstream}/mcp", json=msg, headers=headers)
            print(f"  Status: {resp.status_code}")
            print(f"  Content-Type: {resp.headers.get('content-type')}")
            print(f"  Body (first 500 chars): {resp.text[:500]}")

            if resp.status_code in (200, 202):
                ppass(f"Query executed!")
            else:
                pfail(f"Status: {resp.status_code}")
    except Exception as e:
        pfail(f"Failed: {e}")

    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    url = "http://localhost:8084"
    for arg in sys.argv[1:]:
        if not arg.startswith("-"):
            url = arg
            break

    print(f"URL: {url}")
    print(f"Verbose: {VERBOSE}")

    if DIRECT_SSE:
        print(f"Mode: Direct SSE (no proxy)\n")
        asyncio.run(test_direct_sse(url))
    elif DIRECT_HTTP:
        print(f"Mode: Direct Streamable HTTP (no proxy)\n")
        asyncio.run(test_direct_http(url))
    else:
        print(f"Mode: REST proxy\n")
        success = asyncio.run(test_via_proxy(url))
        sys.exit(0 if success else 1)
