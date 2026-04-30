#!/usr/bin/env python3
"""
VictoriaMetrics MCP Proxy Adapter v5

Proxy que traduz REST /tools/{tool_name} para protocolo MCP.
Detecta automaticamente o modo do upstream (SSE ou Streamable HTTP).

Protocolo MCP Streamable HTTP (/mcp):
  - POST /mcp com JSON-RPC
  - Session ID via header Mcp-Session-Id
  - Initialize gera novo session ID
  - Chamadas seguintes enviam session ID no header

Protocolo MCP SSE (/sse + /message):
  - GET /sse abre stream SSE persistente
  - Server envia endpoint event com message URL
  - POST /message?sessionId=X envia JSON-RPC
  - Respostas via SSE stream

Arquitetura:
  Orchestrator  --REST-->  vm_mcp_proxy.py  --MCP-->  mcp-victoriametrics (Go)
"""

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("vm-mcp-proxy")

VM_MCP_UPSTREAM = os.getenv("VM_MCP_UPSTREAM", "http://localhost:8083")
PROXY_PORT = int(os.getenv("PROXY_LISTEN_PORT", "8084"))
PROXY_TIMEOUT = float(os.getenv("PROXY_TIMEOUT", "60"))


# ---------------------------------------------------------------------------
# MCP Streamable HTTP Client (POST /mcp)
# ---------------------------------------------------------------------------
class MCPHTTPClient:
    """
    Client para MCP Streamable HTTP.
    Initialize gera session ID. Chamadas seguintes enviam no header.
    Respostas podem ser JSON direto ou SSE no body.
    """

    def __init__(self, upstream: str, timeout: float = 60):
        self.upstream = upstream.rstrip("/")
        self.mcp_url = f"{self.upstream}/mcp"
        self.timeout = timeout
        self._session_id: Optional[str] = None
        self._initialized = False

    async def _post(self, method: str, params: dict = None, is_notification: bool = False) -> Optional[dict]:
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not is_notification:
            msg["id"] = str(uuid.uuid4())
        if params:
            msg["params"] = params

        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        async with httpx.AsyncClient(verify=False, timeout=httpx.Timeout(self.timeout)) as http:
            resp = await http.post(self.mcp_url, json=msg, headers=headers)

            # Capturar session ID
            sid = resp.headers.get("mcp-session-id")
            if sid:
                self._session_id = sid

            if is_notification:
                return None

            if resp.status_code not in (200, 202):
                raise RuntimeError(f"MCP returned {resp.status_code}: {resp.text[:200]}")

            ct = resp.headers.get("content-type", "")
            if "application/json" in ct:
                return resp.json()
            if "text/event-stream" in ct:
                return self._parse_sse_body(resp.text, msg.get("id"))
            try:
                return resp.json()
            except Exception:
                return {"result": resp.text[:500]}

    @staticmethod
    def _parse_sse_body(body: str, expected_id: str = None) -> dict:
        for line in body.strip().split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                    if expected_id is None or data.get("id") == expected_id:
                        return data
                except json.JSONDecodeError:
                    continue
        return {"error": "No matching response in SSE body"}

    async def initialize(self):
        if self._initialized:
            return
        result = await self._post("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "vm-mcp-proxy", "version": "5.0.0"},
        })
        log.info(f"[MCPHTTPClient] Initialized | session={self._session_id}")
        await self._post("notifications/initialized", is_notification=True)
        self._initialized = True

    async def list_tools(self) -> list:
        await self.initialize()
        result = await self._post("tools/list")
        return result.get("result", {}).get("tools", []) if result else []

    async def call_tool(self, name: str, arguments: dict) -> dict:
        await self.initialize()
        result = await self._post("tools/call", {"name": name, "arguments": arguments})
        return self._extract_result(result)

    @staticmethod
    def _extract_result(result: Optional[dict]) -> dict:
        if not result:
            return {"success": False, "error": "Empty response"}
        if "result" in result:
            mcp_result = result["result"]
            if "content" in mcp_result:
                texts = [i["text"] for i in mcp_result.get("content", []) if i.get("type") == "text"]
                combined = "\n".join(texts)
                try:
                    return json.loads(combined)
                except json.JSONDecodeError:
                    return {"success": True, "result": combined}
            return {"success": True, "result": mcp_result}
        if "error" in result:
            err = result["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return {"success": False, "error": msg}
        return {"success": True, "result": result}

    def reset(self):
        self._session_id = None
        self._initialized = False


# ---------------------------------------------------------------------------
# MCP SSE Client (GET /sse + POST /message)
# Usa uma sessão por operação para evitar problemas de sessão expirada.
# ---------------------------------------------------------------------------
class MCPSSEClient:
    """
    Client para MCP SSE mode.
    Cada operação: GET /sse → POST /message (initialize) → POST /message (tool call).
    O stream SSE fica aberto durante toda a operação.
    """

    def __init__(self, upstream: str, timeout: float = 60):
        self.upstream = upstream.rstrip("/")
        self.timeout = timeout

    async def list_tools(self) -> list:
        result = await self._execute_with_session("tools/list")
        return result.get("result", {}).get("tools", []) if result else []

    async def call_tool(self, name: str, arguments: dict) -> dict:
        result = await self._execute_with_session("tools/call", {"name": name, "arguments": arguments})
        return MCPHTTPClient._extract_result(result)

    async def _execute_with_session(self, method: str, params: dict = None) -> dict:
        """Abre sessão SSE, initialize, executa método, fecha."""
        pending: Dict[str, asyncio.Future] = {}
        message_url: Optional[str] = None
        connected = asyncio.Event()

        async def sse_reader(http_client: httpx.AsyncClient):
            nonlocal message_url
            try:
                async with http_client.stream("GET", f"{self.upstream}/sse") as resp:
                    resp.raise_for_status()
                    current_event = None
                    async for raw_line in resp.aiter_lines():
                        line = raw_line.strip()
                        if not line:
                            current_event = None
                            continue
                        if line.startswith("event:"):
                            current_event = line[6:].strip()
                            continue
                        if line.startswith("data:"):
                            data_str = line[5:].strip()
                            if current_event == "endpoint" or message_url is None:
                                if data_str.startswith("/"):
                                    message_url = f"{self.upstream}{data_str}"
                                else:
                                    message_url = data_str
                                connected.set()
                            else:
                                try:
                                    msg = json.loads(data_str)
                                    rid = msg.get("id")
                                    if rid and rid in pending:
                                        fut = pending.pop(rid)
                                        if not fut.done():
                                            fut.set_result(msg)
                                except json.JSONDecodeError:
                                    pass
            except asyncio.CancelledError:
                pass
            except Exception as e:
                log.warning(f"[MCPSSEClient] SSE reader error: {e}")
                for fut in pending.values():
                    if not fut.done():
                        fut.set_exception(RuntimeError(f"SSE lost: {e}"))

        async def send_rpc(http_client: httpx.AsyncClient, rpc_method: str, rpc_params: dict = None) -> dict:
            msg_id = str(uuid.uuid4())
            msg = {"jsonrpc": "2.0", "id": msg_id, "method": rpc_method}
            if rpc_params:
                msg["params"] = rpc_params

            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            pending[msg_id] = fut

            resp = await http_client.post(message_url, json=msg, headers={"Content-Type": "application/json"})

            if resp.status_code == 200:
                ct = resp.headers.get("content-type", "")
                if "application/json" in ct:
                    pending.pop(msg_id, None)
                    return resp.json()

            if resp.status_code not in (200, 202):
                pending.pop(msg_id, None)
                raise RuntimeError(f"MCP SSE returned {resp.status_code}: {resp.text[:200]}")

            return await asyncio.wait_for(fut, timeout=self.timeout)

        # Execute with a single SSE connection
        http_client = httpx.AsyncClient(verify=False, timeout=httpx.Timeout(None))
        sse_task = None
        try:
            sse_task = asyncio.create_task(sse_reader(http_client))
            await asyncio.wait_for(connected.wait(), timeout=self.timeout)

            # Initialize
            await send_rpc(http_client, "initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "vm-mcp-proxy", "version": "5.0.0"},
            })

            # Notification (fire and forget)
            notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            try:
                await http_client.post(message_url, json=notif, headers={"Content-Type": "application/json"})
            except Exception:
                pass

            # Execute the actual method
            result = await send_rpc(http_client, method, params)
            return result

        finally:
            if sse_task and not sse_task.done():
                sse_task.cancel()
                try:
                    await sse_task
                except (asyncio.CancelledError, Exception):
                    pass
            await http_client.aclose()

    def reset(self):
        pass  # Stateless — nothing to reset


# ---------------------------------------------------------------------------
# Auto-detect mode + Global client
# ---------------------------------------------------------------------------
_client = None
_client_lock = asyncio.Lock()


async def _detect_mode(upstream: str) -> str:
    """Detecta se o upstream está em modo SSE ou HTTP."""
    async with httpx.AsyncClient(verify=False, timeout=5) as http:
        # Tentar /mcp primeiro (Streamable HTTP)
        try:
            resp = await http.post(
                f"{upstream}/mcp",
                json={"jsonrpc": "2.0", "id": "probe", "method": "initialize",
                       "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                  "clientInfo": {"name": "probe", "version": "1.0.0"}}},
                headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            )
            if resp.status_code in (200, 202):
                log.info(f"[detect_mode] Upstream supports Streamable HTTP (/mcp) | status={resp.status_code}")
                return "http"
        except Exception:
            pass

        # Tentar /sse (SSE mode)
        try:
            resp = await http.get(f"{upstream}/sse", timeout=httpx.Timeout(3))
            if resp.status_code == 200:
                log.info("[detect_mode] Upstream supports SSE (/sse)")
                return "sse"
        except Exception:
            pass

    log.warning("[detect_mode] Could not detect mode, defaulting to http")
    return "http"


async def get_client():
    global _client
    async with _client_lock:
        if _client is None:
            forced_mode = os.getenv("VM_MCP_MODE", "").lower()
            if forced_mode in ("http", "sse"):
                mode = forced_mode
                log.info(f"[get_client] Using forced mode: {mode}")
            else:
                mode = await _detect_mode(VM_MCP_UPSTREAM)

            if mode == "http":
                _client = MCPHTTPClient(VM_MCP_UPSTREAM, PROXY_TIMEOUT)
            else:
                _client = MCPSSEClient(VM_MCP_UPSTREAM, PROXY_TIMEOUT)
            log.info(f"[get_client] Created {type(_client).__name__}")
        return _client


async def reset_client():
    global _client
    async with _client_lock:
        if _client:
            _client.reset()
        _client = None


# ---------------------------------------------------------------------------
# REST Handlers
# ---------------------------------------------------------------------------
async def handle_health(request: Request):
    try:
        async with httpx.AsyncClient(verify=False, timeout=5) as http:
            resp = await http.get(f"{VM_MCP_UPSTREAM}/health/liveness")
            ok = resp.status_code == 200
    except Exception:
        ok = False
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "upstream": VM_MCP_UPSTREAM, "upstream_healthy": ok},
        status_code=200 if ok else 503,
    )


async def handle_list_tools(request: Request):
    try:
        client = await get_client()
        tools = await client.list_tools()
        return JSONResponse({"tools": tools})
    except Exception as e:
        log.error(f"[handle_list_tools] {type(e).__name__}: {e}")
        await reset_client()
        return JSONResponse({"error": str(e)}, status_code=500)


async def handle_tool_call(request: Request):
    tool_name = request.path_params["tool_name"]
    body = await request.json()
    arguments = body.get("arguments", {})

    log.info(f"[handle_tool_call] /tools/{tool_name} | args={json.dumps(arguments)[:200]}")
    start = time.time()

    try:
        client = await get_client()
        result = await client.call_tool(tool_name, arguments)
        elapsed = time.time() - start

        if "success" not in result:
            result = {"success": True, "result": result}
        result["executionTime"] = elapsed

        log.info(f"[handle_tool_call] /tools/{tool_name} OK | time={elapsed:.3f}s")
        return JSONResponse(result)

    except Exception as e:
        elapsed = time.time() - start
        log.error(f"[handle_tool_call] /tools/{tool_name} FAILED | {type(e).__name__}: {e} | time={elapsed:.3f}s")
        await reset_client()
        return JSONResponse(
            {"success": False, "error": str(e), "executionTime": elapsed},
            status_code=500,
        )


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = Starlette(
    routes=[
        Route("/health", endpoint=handle_health),
        Route("/tools", endpoint=handle_list_tools),
        Route("/tools/{tool_name}", endpoint=handle_tool_call, methods=["POST"]),
    ],
)

if __name__ == "__main__":
    log.info(f"Starting VM MCP Proxy v5.0 on port {PROXY_PORT}")
    log.info(f"Upstream: {VM_MCP_UPSTREAM}")
    log.info(f"Timeout: {PROXY_TIMEOUT}s")
    log.info(f"Mode: {os.getenv('VM_MCP_MODE', 'auto-detect')}")
    uvicorn.run(app, host="0.0.0.0", port=PROXY_PORT, log_level="info")
