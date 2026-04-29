#!/usr/bin/env python3
"""
VictoriaMetrics MCP Proxy Adapter v3

Proxy que traduz REST /tools/{tool_name} para protocolo MCP SSE.
Cada request cria uma sessão MCP nova (connect → initialize → call → close).
Simples, robusto, sem race conditions de sessão.

Arquitetura:
  Orchestrator  --REST-->  vm_mcp_proxy.py  --MCP/SSE-->  mcp-victoriametrics (Go)
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
PROXY_TIMEOUT = float(os.getenv("PROXY_TIMEOUT", "30"))


# ---------------------------------------------------------------------------
# MCP SSE Session — uma sessão completa por operação
# ---------------------------------------------------------------------------
class MCPSession:
    """Uma sessão MCP SSE efêmera. Conecta, executa, fecha."""

    def __init__(self, upstream: str, timeout: float = 30):
        self.upstream = upstream.rstrip("/")
        self.timeout = timeout
        self._message_url: Optional[str] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._sse_task: Optional[asyncio.Task] = None
        self._connected = asyncio.Event()
        self._http_sse: Optional[httpx.AsyncClient] = None

    async def open(self):
        """Abre a sessão: inicia SSE stream + initialize handshake."""
        # 1. Abrir SSE stream em background
        self._http_sse = httpx.AsyncClient(verify=False, timeout=httpx.Timeout(None))
        self._sse_task = asyncio.create_task(self._sse_reader())

        try:
            await asyncio.wait_for(self._connected.wait(), timeout=self.timeout)
        except asyncio.TimeoutError:
            await self.close()
            raise RuntimeError(f"Timeout connecting to {self.upstream}/sse")

        # 2. Initialize handshake
        result = await self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "vm-mcp-proxy", "version": "3.0.0"},
        })
        log.debug(f"[MCPSession] Initialized | server={json.dumps(result)[:150]}")

        # 3. Notification
        await self._notify("notifications/initialized")

    async def _sse_reader(self):
        """Lê o SSE stream e despacha respostas."""
        try:
            async with self._http_sse.stream("GET", f"{self.upstream}/sse") as resp:
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
                        if current_event == "endpoint" or self._message_url is None:
                            if data_str.startswith("/"):
                                self._message_url = f"{self.upstream}{data_str}"
                            else:
                                self._message_url = data_str
                            self._connected.set()
                        else:
                            try:
                                msg = json.loads(data_str)
                                rid = msg.get("id")
                                if rid and rid in self._pending:
                                    fut = self._pending.pop(rid)
                                    if not fut.done():
                                        fut.set_result(msg)
                            except json.JSONDecodeError:
                                pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning(f"[MCPSession] SSE reader ended: {type(e).__name__}: {e}")
            # Fail all pending
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError(f"SSE stream lost: {e}"))
            self._pending.clear()

    async def _send(self, method: str, params: dict = None) -> dict:
        """Envia JSON-RPC e espera resposta via SSE."""
        msg_id = str(uuid.uuid4())
        msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params:
            msg["params"] = params

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = fut

        async with httpx.AsyncClient(verify=False, timeout=httpx.Timeout(self.timeout)) as http:
            resp = await http.post(
                self._message_url,
                json=msg,
                headers={"Content-Type": "application/json"},
            )

            if resp.status_code == 200:
                ct = resp.headers.get("content-type", "")
                if "application/json" in ct:
                    self._pending.pop(msg_id, None)
                    return resp.json()

            if resp.status_code not in (200, 202):
                self._pending.pop(msg_id, None)
                body = resp.text[:200]
                raise RuntimeError(f"MCP upstream returned {resp.status_code}: {body}")

        # Esperar resposta via SSE
        try:
            return await asyncio.wait_for(fut, timeout=self.timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise RuntimeError(f"Timeout waiting for MCP response: {method}")

    async def _notify(self, method: str):
        """Envia notificação (sem resposta esperada)."""
        msg = {"jsonrpc": "2.0", "method": method}
        try:
            async with httpx.AsyncClient(verify=False, timeout=httpx.Timeout(5)) as http:
                await http.post(
                    self._message_url,
                    json=msg,
                    headers={"Content-Type": "application/json"},
                )
        except Exception as e:
            log.debug(f"[MCPSession] Notification {method} failed (non-fatal): {e}")

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """Chama uma tool MCP."""
        result = await self._send("tools/call", {"name": name, "arguments": arguments})

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

    async def list_tools(self) -> list:
        """Lista tools disponíveis."""
        result = await self._send("tools/list")
        tools = result.get("result", {}).get("tools", [])
        return tools

    async def close(self):
        """Fecha a sessão."""
        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
            try:
                await self._sse_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._http_sse:
            await self._http_sse.aclose()
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()


# ---------------------------------------------------------------------------
# Helper — executa uma operação com sessão efêmera
# ---------------------------------------------------------------------------
async def with_session(operation, *args, **kwargs):
    """Abre sessão, executa operação, fecha sessão."""
    session = MCPSession(VM_MCP_UPSTREAM, PROXY_TIMEOUT)
    try:
        await session.open()
        return await operation(session, *args, **kwargs)
    finally:
        await session.close()


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
        async def op(session):
            return await session.list_tools()
        tools = await with_session(op)
        return JSONResponse({"tools": tools})
    except Exception as e:
        log.error(f"[handle_list_tools] Error: {type(e).__name__}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


async def handle_tool_call(request: Request):
    tool_name = request.path_params["tool_name"]
    body = await request.json()
    arguments = body.get("arguments", {})

    log.info(f"[handle_tool_call] /tools/{tool_name} | args={json.dumps(arguments)[:200]}")
    start = time.time()

    try:
        async def op(session):
            return await session.call_tool(tool_name, arguments)
        result = await with_session(op)
        elapsed = time.time() - start

        if "success" not in result:
            result = {"success": True, "result": result}
        result["executionTime"] = elapsed

        log.info(f"[handle_tool_call] /tools/{tool_name} OK | time={elapsed:.3f}s")
        return JSONResponse(result)

    except Exception as e:
        elapsed = time.time() - start
        log.error(f"[handle_tool_call] /tools/{tool_name} FAILED | {type(e).__name__}: {e} | time={elapsed:.3f}s")
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
    log.info(f"Starting VM MCP Proxy v3.0 on port {PROXY_PORT}")
    log.info(f"Upstream: {VM_MCP_UPSTREAM}")
    log.info(f"Timeout: {PROXY_TIMEOUT}s")
    uvicorn.run(app, host="0.0.0.0", port=PROXY_PORT, log_level="info")
