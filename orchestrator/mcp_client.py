"""HTTP client for communicating with MCP servers."""

import json
import logging
from typing import Any, Dict

import httpx

log = logging.getLogger("orchestrator")


class MCPClient:
    """Client for communicating with MCP servers via HTTP."""

    def __init__(self, server_name: str, endpoint: str, timeout: int = 15):
        self.server_name = server_name
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self._request_id = 1
        self._mcp_session_id = None
        # verify=False para ambientes corporativos com proxy/certificados internos
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            verify=False
        )

    def _next_request_id(self) -> int:
        request_id = self._request_id
        self._request_id += 1
        return request_id

    def _tempo_mcp_url(self) -> str:
        if self.endpoint.endswith("/api/mcp"):
            return self.endpoint
        return f"{self.endpoint}/api/mcp"

    def _is_mcp_http_endpoint(self) -> bool:
        return self.endpoint.endswith("/mcp") or self.endpoint.endswith("/api/mcp")

    def _mcp_http_url(self) -> str:
        # Tempo keeps compatibility with its /api/mcp endpoint.
        if self.server_name == "tempo":
            return self._tempo_mcp_url()
        if self._is_mcp_http_endpoint():
            return self.endpoint
        return f"{self.endpoint}/mcp"

    def _should_use_mcp_jsonrpc_http(self) -> bool:
        # Generic MCP HTTP mode is enabled when endpoint already points to /mcp.
        return self.server_name == "tempo" or self._is_mcp_http_endpoint()

    async def _ensure_mcp_session(self):
        if self._mcp_session_id:
            return

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "orchestrator", "version": "1.0.0"},
            },
        }

        response = await self.client.post(
            self._mcp_http_url(),
            headers={"Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()

        session_id = response.headers.get("Mcp-Session-Id")
        # Streamable HTTP MCP may omit session header; include it only when returned.
        self._mcp_session_id = session_id

    async def _call_tool_mcp_jsonrpc_http(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        await self._ensure_mcp_session()

        tool_call_name = tool_name.replace("_", "-") if self.server_name == "tempo" else tool_name

        rpc_payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "tools/call",
            "params": {
                "name": tool_call_name,
                "arguments": arguments,
            },
        }

        headers = {"Content-Type": "application/json"}
        if self._mcp_session_id:
            headers["Mcp-Session-Id"] = self._mcp_session_id

        response = await self.client.post(
            self._mcp_http_url(),
            headers=headers,
            json=rpc_payload,
        )
        response.raise_for_status()

        rpc_response = response.json()
        if "error" in rpc_response:
            raise RuntimeError(
                f"MCP tools/call error: {json.dumps(rpc_response['error'], ensure_ascii=True)}"
            )

        return rpc_response.get("result", {})

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        import time
        start_time = time.time()
        
        log.info(
            f"[MCPClient.call_tool] Starting MCP call | "
            f"server={self.server_name} | "
            f"tool={tool_name} | "
            f"endpoint={self.endpoint} | "
            f"timeout={self.timeout}s | "
            f"arguments={arguments}"
        )
        
        try:
            if self._should_use_mcp_jsonrpc_http():
                result = await self._call_tool_mcp_jsonrpc_http(tool_name, arguments)
            else:
                try:
                    response = await self.client.post(
                        f"{self.endpoint}/tools/{tool_name}",
                        json={"arguments": arguments},
                    )
                    response.raise_for_status()
                    result = response.json()
                except httpx.HTTPStatusError as e:
                    if e.response.status_code not in (404, 405):
                        raise

                    log.info(
                        f"[MCPClient.call_tool] Legacy endpoint returned {e.response.status_code}; "
                        f"retrying with MCP JSON-RPC HTTP at {self._mcp_http_url()}"
                    )
                    result = await self._call_tool_mcp_jsonrpc_http(tool_name, arguments)
            
            execution_time = time.time() - start_time
            mcp_execution_time = result.get('executionTime', 0)
            
            log.info(
                f"[MCPClient.call_tool] MCP call completed | "
                f"server={self.server_name} | "
                f"tool={tool_name} | "
                f"total_time={execution_time:.3f}s | "
                f"mcp_execution_time={mcp_execution_time:.3f}s | "
                f"network_overhead={execution_time - mcp_execution_time:.3f}s | "
                f"result_size={len(json.dumps(result, default=str))} bytes"
            )
            
            return result
            
        except httpx.TimeoutException as e:
            execution_time = time.time() - start_time
            log.error(
                f"[MCPClient.call_tool] Timeout | "
                f"server={self.server_name} | "
                f"tool={tool_name} | "
                f"timeout={self.timeout}s | "
                f"elapsed={execution_time:.3f}s"
            )
            return {
                "success": False,
                "error": f"Timeout after {self.timeout}s",
                "executionTime": self.timeout
            }
            
        except httpx.HTTPStatusError as e:
            execution_time = time.time() - start_time
            log.error(
                f"[MCPClient.call_tool] HTTP error | "
                f"server={self.server_name} | "
                f"tool={tool_name} | "
                f"status_code={e.response.status_code} | "
                f"elapsed={execution_time:.3f}s | "
                f"response_text={e.response.text[:200]}"
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                "executionTime": 0
            }
            
        except Exception as e:
            execution_time = time.time() - start_time
            log.error(
                f"[MCPClient.call_tool] Unexpected error | "
                f"server={self.server_name} | "
                f"tool={tool_name} | "
                f"elapsed={execution_time:.3f}s | "
                f"error_type={type(e).__name__} | "
                f"error={str(e)[:200]}"
            )
            return {
                "success": False,
                "error": str(e),
                "executionTime": 0
            }

    async def close(self):
        self._mcp_session_id = None
        await self.client.aclose()