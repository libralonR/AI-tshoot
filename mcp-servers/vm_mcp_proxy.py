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
"""

import asyncio
import json
import logging
import os
import sys
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
    level=logging.DEBUG,
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
# MCP SSE Client — conecta ao VM MCP via protocolo MCP sobre SSE
# ---------------------------------------------------------------------------
class MCPSSEClient:
    """Client que fala protocolo MCP via SSE com o mcp-victoriametrics."""

    def __init__(self, upstream: str, timeout: float = 30):
        self.upstream = upstream.rstrip("/")
        self.timeout = timeout
        self._session_url: Optional[str] = None
        self._http = httpx.AsyncClient(verify=False, timeout=httpx.Timeout(timeout))
        self._initialized = False
        self._tools: List[Dict[str, Any]] = []

    async def close(self):
        await self._http.aclose()

    # ---- handshake: GET /sse → recebe endpoint, POST initialize ----

    async def _ensure_session(self):
        """Conecta ao SSE endpoint e obtém a URL de mensagens."""
        if self._session_url:
            return

        log.info(f"[MCPSSEClient] Connecting to SSE endpoint: {self.upstream}/sse")
        async with self._http.stream("GET", f"{self.upstream}/sse") as resp:
            async for line in resp.aiter_lines():
                line = line.strip()
                if line.startswith("event: endpoint"):
                    continue
                if line.startswith("data: "):
                    endpoint = line[6:].strip()
                    # endpoint pode ser relativo (/message?...) ou absoluto
                    if endpoint.startswith("/"):
                        self._session_url = f"{self.upstream}{endpoint}"
                    else:
                        self._session_url = endpoint
                    log.info(f"[MCPSSEClient] Session URL: {self._session_url}")
                    break

        if not self._session_url:
            raise RuntimeError("Failed to obtain MCP session URL from SSE endpoint")

    async def _send_jsonrpc(self, method: str, params: dict = None) -> dict:
        """Envia uma mensagem JSON-RPC para o MCP server."""
        await self._ensure_session()

        msg = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
        }
        if params:
            msg["params"] = params

        log.debug(f"[MCPSSEClient] Sending JSON-RPC: method={method}")
        resp = await self._http.post(
            self._session_url,
            json=msg,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()

        # O MCP SSE retorna a resposta via SSE stream no mesmo POST
        # ou como JSON direto dependendo da implementação
        content_type = resp.headers.get("content-type", "")

        if "application/json" in content_type:
            return resp.json()

        # Para SSE, precisamos parsear o stream
        result = None
        for line in resp.text.strip().split("\n"):
            line = line.strip()
            if line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                    if data.get("id") == msg["id"]:
                        result = data
                        break
                    # Pode ser uma notificação, ignorar
                except json.JSONDecodeError:
                    continue

        if result:
            return result

        # Fallback: tentar parsear o body inteiro como JSON
        try:
            return resp.json()
        except Exception:
            return {"error": f"Unexpected response: {resp.text[:200]}"}

    async def initialize(self):
        """Envia initialize + initialized para o MCP server."""
        if self._initialized:
            return

        result = await self._send_jsonrpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "vm-mcp-proxy", "version": "1.0.0"},
        })
        log.info(f"[MCPSSEClient] Initialize response: {json.dumps(result)[:200]}")

        # Enviar notifications/initialized
        await self._ensure_session()
        notif = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        try:
            await self._http.post(
                self._session_url,
                json=notif,
                headers={"Content-Type": "application/json"},
            )
        except Exception as e:
            log.warning(f"[MCPSSEClient] notifications/initialized failed (non-fatal): {e}")

        self._initialized = True

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Lista as tools disponíveis no MCP server."""
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
        """Chama uma tool no MCP server."""
        await self.initialize()
        result = await self._send_jsonrpc("tools/call", {
            "name": name,
            "arguments": arguments,
        })

        # Extrair o conteúdo da resposta MCP
        if "result" in result:
            mcp_result = result["result"]
            # MCP retorna content como lista de {type, text}
            if "content" in mcp_result:
                texts = []
                for item in mcp_result["content"]:
                    if item.get("type") == "text":
                        texts.append(item["text"])
                combined = "\n".join(texts)
                # Tentar parsear como JSON
                try:
                    return json.loads(combined)
                except json.JSONDecodeError:
                    return {"success": True, "result": combined}
            return mcp_result

        if "error" in result:
            return {"success": False, "error": result["error"].get("message", str(result["error"]))}

        return {"success": True, "result": result}


# ---------------------------------------------------------------------------
# Streamable HTTP Client — alternativa para modo http
# ---------------------------------------------------------------------------
class MCPStreamableHTTPClient:
    """Client que fala protocolo MCP via Streamable HTTP (/mcp endpoint)."""

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
        """Envia JSON-RPC via POST /mcp."""
        msg = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
        }
        if params:
            msg["params"] = params

        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        log.debug(f"[MCPHTTPClient] Sending JSON-RPC: method={method}")
        resp = await self._http.post(
            f"{self.upstream}/mcp",
            json=msg,
            headers=headers,
        )
        resp.raise_for_status()

        # Capturar session ID
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid

        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            return resp.json()

        # SSE response
        for line in resp.text.strip().split("\n"):
            line = line.strip()
            if line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
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
            "clientInfo": {"name": "vm-mcp-proxy", "version": "1.0.0"},
        })
        log.info(f"[MCPHTTPClient] Initialize response: {json.dumps(result)[:200]}")

        # notifications/initialized
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        headers = {"Content-Type": "application/json"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        try:
            await self._http.post(f"{self.upstream}/mcp", json=notif, headers=headers)
        except Exception as e:
            log.warning(f"[MCPHTTPClient] notifications/initialized failed (non-fatal): {e}")

        self._initialized = True

    async def list_tools(self) -> List[Dict[str, Any]]:
        await self.initialize()
        result = await self._send_jsonrpc("tools/list")
        tools = []
        if "result" in result and "tools" in result["result"]:
            tools = result["result"]["tools"]
        elif "tools" in result:
            tools = result["tools"]
        self._tools = tools
        log.info(f"[MCPHTTPClient] Listed {len(tools)} tools")
        return tools

    async def call_tool(self, name: str, arguments: dict) -> dict:
        await self.initialize()
        result = await self._send_jsonrpc("tools/call", {
            "name": name,
            "arguments": arguments,
        })
        if "result" in result:
            mcp_result = result["result"]
            if "content" in mcp_result:
                texts = []
                for item in mcp_result["content"]:
                    if item.get("type") == "text":
                        texts.append(item["text"])
                combined = "\n".join(texts)
                try:
                    return json.loads(combined)
                except json.JSONDecodeError:
                    return {"success": True, "result": combined}
            return mcp_result
        if "error" in result:
            return {"success": False, "error": result["error"].get("message", str(result["error"]))}
        return {"success": True, "result": result}


# ---------------------------------------------------------------------------
# Factory — escolhe o client baseado no modo do upstream
# ---------------------------------------------------------------------------
def create_mcp_client(upstream: str, timeout: float = 30):
    """Cria o client MCP apropriado baseado no VM_MCP_MODE."""
    mode = os.getenv("VM_MCP_MODE", "sse").lower()
    if mode == "http":
        log.info(f"[create_mcp_client] Using Streamable HTTP client | upstream={upstream}")
        return MCPStreamableHTTPClient(upstream, timeout)
    else:
        log.info(f"[create_mcp_client] Using SSE client | upstream={upstream}")
        return MCPSSEClient(upstream, timeout)


# ---------------------------------------------------------------------------
# Global MCP client (reusado entre requests)
# ---------------------------------------------------------------------------
_mcp_client = None


async def get_mcp_client():
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = create_mcp_client(VM_MCP_UPSTREAM, PROXY_TIMEOUT)
    return _mcp_client


# ---------------------------------------------------------------------------
# REST API Handlers — mesma interface que Grafana MCP e Incidents PG MCP
# ---------------------------------------------------------------------------
async def handle_health(request: Request):
    """Health check — verifica conectividade com o upstream."""
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
    """Lista tools disponíveis no VM MCP."""
    try:
        client = await get_mcp_client()
        tools = await client.list_tools()
        return JSONResponse({"tools": tools})
    except Exception as e:
        log.exception(f"[handle_list_tools] Error listing tools")
        return JSONResponse({"error": str(e)}, status_code=500)


async def handle_tool_call(request: Request):
    """Executa uma tool — mesma interface que os outros MCPs."""
    tool_name = request.path_params["tool_name"]
    body = await request.json()
    arguments = body.get("arguments", {})

    log.info(f"[handle_tool_call] REST /tools/{tool_name} | arguments={arguments}")
    start_time = time.time()

    try:
        client = await get_mcp_client()
        result = await client.call_tool(tool_name, arguments)
        execution_time = time.time() - start_time

        # Normalizar resposta para o formato esperado pelo orchestrator
        if "success" not in result:
            result = {"success": True, "result": result}
        result["executionTime"] = execution_time

        log.info(
            f"[handle_tool_call] REST /tools/{tool_name} completed | "
            f"success={result.get('success')} | "
            f"execution_time={execution_time:.3f}s"
        )
        return JSONResponse(result)

    except Exception as e:
        execution_time = time.time() - start_time
        log.exception(f"[handle_tool_call] REST /tools/{tool_name} error")
        return JSONResponse(
            {"success": False, "error": str(e), "executionTime": execution_time},
            status_code=500,
        )


# ---------------------------------------------------------------------------
# Starlette App
# ---------------------------------------------------------------------------
app = Starlette(
    routes=[
        Route("/health", endpoint=handle_health),
        Route("/tools", endpoint=handle_list_tools),
        Route("/tools/{tool_name}", endpoint=handle_tool_call, methods=["POST"]),
    ],
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    log.info(f"Starting VM MCP Proxy Adapter on port {PROXY_PORT}")
    log.info(f"Upstream VM MCP: {VM_MCP_UPSTREAM}")
    log.info(f"Mode: {os.getenv('VM_MCP_MODE', 'sse')}")
    uvicorn.run(app, host="0.0.0.0", port=PROXY_PORT, log_level="info")
