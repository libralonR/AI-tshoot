#!/usr/bin/env python3
"""
VictoriaMetrics MCP Proxy Adapter

Proxy que traduz a API REST /tools/{tool_name} (padrão do orchestrator)
para o protocolo MCP SSE do mcp-victoriametrics (Go binary).

Arquitetura:
  Orchestrator  --REST-->  vm_mcp_proxy.py  --MCP/SSE-->  mcp-victoriametrics (Go)

O proxy expõe os mesmos endpoints que os outros MCP servers (Grafana, Incidents PG):
  - GET  /health
  - GET  /tools          (lista tools disponíveis)
  - POST /tools/{name}   (executa uma tool)

Protocolo MCP SSE:
  1. GET /sse → abre stream SSE persistente, recebe sessionId
  2. POST /message?sessionId=X → envia JSON-RPC
  3. Respostas chegam via stream SSE (não no POST response)
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

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("vm-mcp-proxy")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
VM_MCP_UPSTREAM = os.getenv("VM_MCP_UPSTREAM", "http://localhost:8083")
PROXY_PORT = int(os.getenv("PROXY_LISTEN_PORT", "8084"))
PROXY_TIMEOUT = float(os.getenv("PROXY_TIMEOUT", "30"))


# ---------------------------------------------------------------------------
# MCP SSE Client — mantém stream SSE aberto em background
# ---------------------------------------------------------------------------
class MCPSSEClient:
    """Client MCP via SSE. Mantém o stream SSE aberto para receber respostas."""

    def __init__(self, upstream: str, timeout: float = 30):
        self.upstream = upstream.rstrip("/")
        self.timeout = timeout
        self._message_url: Optional[str] = None
        self._initialized = False
        self._tools: List[Dict[str, Any]] = []
        # Pending responses: request_id → asyncio.Future
        self._pending: Dict[str, asyncio.Future] = {}
        self._sse_task: Optional[asyncio.Task] = None
        self._connected = asyncio.Event()
        self._closed = False

    async def connect(self):
        """Inicia a conexão SSE em background."""
        if self._sse_task is not None:
            return
        self._sse_task = asyncio.create_task(self._sse_loop())
        # Esperar até receber o endpoint
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=self.timeout)
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Timeout connecting to MCP SSE endpoint: {self.upstream}/sse"
            )
        log.info(f"[MCPSSEClient] Connected | message_url={self._message_url}")

    async def _sse_loop(self):
        """Loop que mantém o stream SSE aberto e despacha respostas."""
        while not self._closed:
            try:
                log.info(f"[MCPSSEClient] Opening SSE stream: {self.upstream}/sse")
                async with httpx.AsyncClient(
                    verify=False, timeout=httpx.Timeout(None)
                ) as http:
                    async with http.stream("GET", f"{self.upstream}/sse") as resp:
                        resp.raise_for_status()
                        current_event = None
                        async for line in resp.aiter_lines():
                            if self._closed:
                                return
                            line = line.strip()
                            if not line:
                                current_event = None
                                continue
                            if line.startswith("event:"):
                                current_event = line[6:].strip()
                                continue
                            if line.startswith("data:"):
                                data_str = line[5:].strip()
                                if current_event == "endpoint" or self._message_url is None:
                                    # Primeiro data é o endpoint
                                    if data_str.startswith("/"):
                                        self._message_url = f"{self.upstream}{data_str}"
                                    else:
                                        self._message_url = data_str
                                    log.info(f"[MCPSSEClient] Got message URL: {self._message_url}")
                                    self._connected.set()
                                else:
                                    # Resposta JSON-RPC
                                    self._dispatch_response(data_str)
            except asyncio.CancelledError:
                return
            except Exception as e:
                if self._closed:
                    return
                log.error(f"[MCPSSEClient] SSE stream error: {type(e).__name__}: {e}")
                # Reset state e reconectar
                self._message_url = None
                self._connected.clear()
                self._initialized = False
                # Falhar todos os pending
                for req_id, fut in list(self._pending.items()):
                    if not fut.done():
                        fut.set_exception(RuntimeError(f"SSE stream lost: {e}"))
                self._pending.clear()
                await asyncio.sleep(2)

    def _dispatch_response(self, data_str: str):
        """Despacha uma resposta JSON-RPC para o Future correspondente."""
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            log.warning(f"[MCPSSEClient] Non-JSON SSE data: {data_str[:100]}")
            return

        req_id = data.get("id")
        if req_id and req_id in self._pending:
            fut = self._pending.pop(req_id)
            if not fut.done():
                fut.set_result(data)
                log.debug(f"[MCPSSEClient] Dispatched response for id={req_id}")
        else:
            # Notificação ou resposta sem pending (ex: heartbeat)
            log.debug(f"[MCPSSEClient] Unmatched SSE data: id={req_id}")

    async def _send_jsonrpc(self, method: str, params: dict = None) -> dict:
        """Envia JSON-RPC via POST e espera resposta via SSE stream."""
        await self.connect()

        msg_id = str(uuid.uuid4())
        msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params:
            msg["params"] = params

        # Criar Future para a resposta
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[msg_id] = fut

        log.debug(f"[MCPSSEClient] Sending JSON-RPC: method={method} id={msg_id[:8]}")

        try:
            async with httpx.AsyncClient(
                verify=False, timeout=httpx.Timeout(self.timeout)
            ) as http:
                resp = await http.post(
                    self._message_url,
                    json=msg,
                    headers={"Content-Type": "application/json"},
                )

                if resp.status_code == 400:
                    body = resp.text
                    log.warning(
                        f"[MCPSSEClient] 400 from upstream | method={method} | body={body[:200]}"
                    )
                    # Session expired — reset e retry
                    self._pending.pop(msg_id, None)
                    await self._reset()
                    return await self._send_jsonrpc(method, params)

                if resp.status_code == 202:
                    # Accepted — resposta virá via SSE stream
                    log.debug(f"[MCPSSEClient] 202 Accepted, waiting for SSE response")
                elif resp.status_code == 200:
                    # Resposta direta no POST (alguns servers fazem isso)
                    content_type = resp.headers.get("content-type", "")
                    if "application/json" in content_type:
                        result = resp.json()
                        self._pending.pop(msg_id, None)
                        return result
                else:
                    resp.raise_for_status()

            # Esperar resposta via SSE
            result = await asyncio.wait_for(fut, timeout=self.timeout)
            return result

        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise RuntimeError(
                f"Timeout waiting for MCP response: method={method} timeout={self.timeout}s"
            )
        except Exception:
            self._pending.pop(msg_id, None)
            raise

    async def _send_notification(self, method: str, params: dict = None):
        """Envia notificação JSON-RPC (sem id, sem resposta esperada)."""
        await self.connect()
        msg = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params

        try:
            async with httpx.AsyncClient(
                verify=False, timeout=httpx.Timeout(self.timeout)
            ) as http:
                resp = await http.post(
                    self._message_url,
                    json=msg,
                    headers={"Content-Type": "application/json"},
                )
                log.debug(f"[MCPSSEClient] Notification {method}: status={resp.status_code}")
        except Exception as e:
            log.warning(f"[MCPSSEClient] Notification {method} failed (non-fatal): {e}")

    async def _reset(self):
        """Reset session state para reconexão."""
        log.warning("[MCPSSEClient] Resetting session")
        self._message_url = None
        self._connected.clear()
        self._initialized = False
        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
            try:
                await self._sse_task
            except (asyncio.CancelledError, Exception):
                pass
        self._sse_task = None

    async def close(self):
        """Fecha o client."""
        self._closed = True
        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
            try:
                await self._sse_task
            except (asyncio.CancelledError, Exception):
                pass
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def initialize(self):
        """Envia initialize + initialized para o MCP server."""
        if self._initialized:
            return

        log.info("[MCPSSEClient] Sending initialize...")
        result = await self._send_jsonrpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "vm-mcp-proxy", "version": "2.0.0"},
        })
        log.info(f"[MCPSSEClient] Initialize OK | response={json.dumps(result)[:200]}")

        await self._send_notification("notifications/initialized")
        self._initialized = True

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Lista tools disponíveis."""
        await self.initialize()
        result = await self._send_jsonrpc("tools/list")

        tools = []
        if "result" in result and "tools" in result["result"]:
            tools = result["result"]["tools"]
        elif "tools" in result:
            tools = result["tools"]

        self._tools = tools
        log.info(f"[MCPSSEClient] Listed {len(tools)} tools")
        return tools

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """Chama uma tool."""
        await self.initialize()

        log.info(f"[MCPSSEClient] Calling tool: {name} | args={json.dumps(arguments)[:200]}")
        result = await self._send_jsonrpc("tools/call", {
            "name": name,
            "arguments": arguments,
        })

        # Extrair conteúdo da resposta MCP
        if "result" in result:
            mcp_result = result["result"]
            if "content" in mcp_result:
                texts = []
                for item in mcp_result.get("content", []):
                    if item.get("type") == "text":
                        texts.append(item["text"])
                combined = "\n".join(texts)
                try:
                    return json.loads(combined)
                except json.JSONDecodeError:
                    return {"success": True, "result": combined}
            return mcp_result

        if "error" in result:
            err = result["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return {"success": False, "error": msg}

        return {"success": True, "result": result}


# ---------------------------------------------------------------------------
# Streamable HTTP Client — alternativa para modo http (/mcp endpoint)
# ---------------------------------------------------------------------------
class MCPStreamableHTTPClient:
    """Client MCP via Streamable HTTP. Mais simples que SSE."""

    def __init__(self, upstream: str, timeout: float = 30):
        self.upstream = upstream.rstrip("/")
        self.timeout = timeout
        self._http = httpx.AsyncClient(verify=False, timeout=httpx.Timeout(timeout))
        self._initialized = False
        self._tools: List[Dict[str, Any]] = []
        self._session_id: Optional[str] = None

    async def close(self):
        await self._http.aclose()

    async def _send_jsonrpc(self, method: str, params: dict = None) -> dict:
        msg = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
        }
        if params:
            msg["params"] = params

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        log.debug(f"[MCPHTTPClient] Sending: method={method}")
        resp = await self._http.post(f"{self.upstream}/mcp", json=msg, headers=headers)
        resp.raise_for_status()

        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid

        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            return resp.json()

        # Parse SSE response
        for line in resp.text.strip().split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                    if data.get("id") == msg["id"]:
                        return data
                except json.JSONDecodeError:
                    continue

        try:
            return resp.json()
        except Exception:
            return {"error": f"Unexpected response: {resp.text[:200]}"}

    async def initialize(self):
        if self._initialized:
            return
        result = await self._send_jsonrpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "vm-mcp-proxy", "version": "2.0.0"},
        })
        log.info(f"[MCPHTTPClient] Initialize OK")

        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        headers = {"Content-Type": "application/json"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        try:
            await self._http.post(f"{self.upstream}/mcp", json=notif, headers=headers)
        except Exception as e:
            log.warning(f"[MCPHTTPClient] notifications/initialized failed: {e}")
        self._initialized = True

    async def list_tools(self) -> List[Dict[str, Any]]:
        await self.initialize()
        result = await self._send_jsonrpc("tools/list")
        tools = result.get("result", {}).get("tools", [])
        self._tools = tools
        log.info(f"[MCPHTTPClient] Listed {len(tools)} tools")
        return tools

    async def call_tool(self, name: str, arguments: dict) -> dict:
        await self.initialize()
        result = await self._send_jsonrpc("tools/call", {"name": name, "arguments": arguments})
        if "result" in result:
            mcp_result = result["result"]
            if "content" in mcp_result:
                texts = [i["text"] for i in mcp_result.get("content", []) if i.get("type") == "text"]
                combined = "\n".join(texts)
                try:
                    return json.loads(combined)
                except json.JSONDecodeError:
                    return {"success": True, "result": combined}
            return mcp_result
        if "error" in result:
            err = result["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return {"success": False, "error": msg}
        return {"success": True, "result": result}


# ---------------------------------------------------------------------------
# Factory + Global client
# ---------------------------------------------------------------------------
def create_mcp_client(upstream: str, timeout: float = 30):
    mode = os.getenv("VM_MCP_MODE", "sse").lower()
    if mode == "http":
        log.info(f"[create_mcp_client] Using Streamable HTTP client | upstream={upstream}")
        return MCPStreamableHTTPClient(upstream, timeout)
    else:
        log.info(f"[create_mcp_client] Using SSE client | upstream={upstream}")
        return MCPSSEClient(upstream, timeout)


_mcp_client: Optional[Any] = None
_mcp_lock = asyncio.Lock()


async def get_mcp_client():
    global _mcp_client
    async with _mcp_lock:
        if _mcp_client is None:
            _mcp_client = create_mcp_client(VM_MCP_UPSTREAM, PROXY_TIMEOUT)
        return _mcp_client


async def reset_mcp_client():
    global _mcp_client
    async with _mcp_lock:
        if _mcp_client is not None:
            try:
                await _mcp_client.close()
            except Exception:
                pass
            _mcp_client = None


# ---------------------------------------------------------------------------
# REST API Handlers
# ---------------------------------------------------------------------------
async def handle_health(request: Request):
    try:
        async with httpx.AsyncClient(verify=False, timeout=5) as client:
            resp = await client.get(f"{VM_MCP_UPSTREAM}/health/liveness")
            upstream_ok = resp.status_code == 200
    except Exception:
        upstream_ok = False

    status = "ok" if upstream_ok else "degraded"
    return JSONResponse(
        {"status": status, "upstream": VM_MCP_UPSTREAM, "upstream_healthy": upstream_ok},
        status_code=200 if upstream_ok else 503,
    )


async def handle_list_tools(request: Request):
    try:
        client = await get_mcp_client()
        tools = await client.list_tools()
        return JSONResponse({"tools": tools})
    except Exception as e:
        log.exception("[handle_list_tools] Error")
        await reset_mcp_client()
        return JSONResponse({"error": str(e)}, status_code=500)


async def handle_tool_call(request: Request):
    tool_name = request.path_params["tool_name"]
    body = await request.json()
    arguments = body.get("arguments", {})

    log.info(f"[handle_tool_call] /tools/{tool_name} | args={json.dumps(arguments)[:200]}")
    start_time = time.time()

    try:
        client = await get_mcp_client()
        result = await client.call_tool(tool_name, arguments)
        elapsed = time.time() - start_time

        if "success" not in result:
            result = {"success": True, "result": result}
        result["executionTime"] = elapsed

        log.info(
            f"[handle_tool_call] /tools/{tool_name} OK | "
            f"success={result.get('success')} | time={elapsed:.3f}s"
        )
        return JSONResponse(result)

    except Exception as e:
        elapsed = time.time() - start_time
        log.error(
            f"[handle_tool_call] /tools/{tool_name} FAILED | "
            f"error={type(e).__name__}: {str(e)[:200]} | time={elapsed:.3f}s"
        )
        # Reset client para próxima tentativa
        await reset_mcp_client()
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
    log.info(f"Starting VM MCP Proxy v2.0 on port {PROXY_PORT}")
    log.info(f"Upstream: {VM_MCP_UPSTREAM}")
    log.info(f"Mode: {os.getenv('VM_MCP_MODE', 'sse')}")
    log.info(f"Timeout: {PROXY_TIMEOUT}s")
    uvicorn.run(app, host="0.0.0.0", port=PROXY_PORT, log_level="info")
