"""HTTP client para MCP servers.

Suporta tanto o protocolo REST customizado (`POST /tools/{name}`) usado pelos
MCPs Python (Grafana, Incidents PG, vm_mcp_proxy) quanto o protocolo MCP
JSON-RPC nativo (`POST /mcp` ou `/api/mcp`) usado pelos MCPs Go (Tempo,
VictoriaMetrics oficial).

Idêntico ao `orchestrator/mcp_client.py` da versão atual.
"""

import json
import logging
import time
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
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            verify=False,
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

    def _is_tempo_native(self) -> bool:
        """Detecta se Tempo está em modo API nativa (sem wrapper MCP)."""
        if self.server_name != "tempo":
            return False
        return not self._is_mcp_http_endpoint()

    def _mcp_http_url(self) -> str:
        if self.server_name == "tempo" and not self._is_tempo_native():
            return self._tempo_mcp_url()
        if self._is_mcp_http_endpoint():
            return self.endpoint
        return f"{self.endpoint}/mcp"

    def _should_use_mcp_jsonrpc_http(self) -> bool:
        if self._is_tempo_native():
            return False
        return self.server_name == "tempo" or self._is_mcp_http_endpoint()

    async def _call_tempo_native(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Chama a API REST nativa do Grafana Tempo (sem wrapper MCP)."""
        base = self.endpoint

        if tool_name == "traceql-search":
            params = {"q": arguments.get("query", "")}
            if arguments.get("limit"):
                params["limit"] = str(arguments["limit"])
            if arguments.get("start"):
                params["start"] = str(arguments["start"])
            if arguments.get("end"):
                params["end"] = str(arguments["end"])
            response = await self.client.get(f"{base}/api/search", params=params)

        elif tool_name == "get-trace":
            trace_id = arguments.get("trace_id", "")
            response = await self.client.get(f"{base}/api/traces/{trace_id}")

        elif tool_name == "traceql-metrics-instant":
            params = {"q": arguments.get("query", "")}
            if arguments.get("time"):
                params["time"] = str(arguments["time"])
            response = await self.client.get(f"{base}/api/metrics/query", params=params)

        elif tool_name == "traceql-metrics-range":
            params = {"q": arguments.get("query", "")}
            if arguments.get("start"):
                params["start"] = str(arguments["start"])
            if arguments.get("end"):
                params["end"] = str(arguments["end"])
            if arguments.get("step"):
                params["step"] = str(arguments["step"])
            response = await self.client.get(f"{base}/api/metrics/query_range", params=params)

        elif tool_name == "get-attribute-names":
            params = {}
            if arguments.get("scope"):
                params["scope"] = arguments["scope"]
            response = await self.client.get(f"{base}/api/v2/search/tags", params=params)

        elif tool_name == "get-attribute-values":
            scope = arguments.get("scope", "span")
            attr = arguments.get("attribute", "")
            tag_name = f"{scope}.{attr}" if scope else attr
            response = await self.client.get(f"{base}/api/v2/search/tag/{tag_name}/values")

        elif tool_name == "docs-traceql":
            return {
                "success": True,
                "result": (
                    "TraceQL documentation: use { resource.service.name = \"svc\" } for "
                    "service filtering, { status = error } for errors, { duration > 1s } "
                    "for slow spans. Combine with && for AND. Aggregations: | count(), "
                    "| rate(), | quantile_over_time(duration, 0.99). Structural: { a } > { b } "
                    "(parent-child), { a } >> { b } (ancestor-descendant)."
                ),
            }
        else:
            return {"success": False, "error": f"Unknown Tempo tool: {tool_name}"}

        response.raise_for_status()
        data = response.json()
        return {"success": True, "result": data}

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
        self._mcp_session_id = response.headers.get("Mcp-Session-Id")

    async def _call_tool_mcp_jsonrpc_http(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        await self._ensure_mcp_session()

        tool_call_name = (
            tool_name.replace("_", "-") if self.server_name == "tempo" else tool_name
        )

        rpc_payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "tools/call",
            "params": {"name": tool_call_name, "arguments": arguments},
        }

        headers = {"Content-Type": "application/json"}
        if self._mcp_session_id:
            headers["Mcp-Session-Id"] = self._mcp_session_id

        response = await self.client.post(
            self._mcp_http_url(), headers=headers, json=rpc_payload
        )
        response.raise_for_status()

        rpc_response = response.json()
        if "error" in rpc_response:
            raise RuntimeError(
                f"MCP tools/call error: {json.dumps(rpc_response['error'], ensure_ascii=True)}"
            )
        return rpc_response.get("result", {})

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()
        log.info(
            f"[MCPClient.call_tool] Starting | server={self.server_name} | "
            f"tool={tool_name} | endpoint={self.endpoint} | timeout={self.timeout}s"
        )

        try:
            if self._is_tempo_native():
                result = await self._call_tempo_native(tool_name, arguments)
            elif self._should_use_mcp_jsonrpc_http():
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
                        f"[MCPClient.call_tool] Legacy endpoint returned "
                        f"{e.response.status_code}; retrying with MCP JSON-RPC HTTP at "
                        f"{self._mcp_http_url()}"
                    )
                    result = await self._call_tool_mcp_jsonrpc_http(tool_name, arguments)

            execution_time = time.time() - start_time
            log.info(
                f"[MCPClient.call_tool] Completed | server={self.server_name} | "
                f"tool={tool_name} | total_time={execution_time:.3f}s | "
                f"result_size={len(json.dumps(result, default=str))} bytes"
            )
            return result

        except httpx.TimeoutException:
            execution_time = time.time() - start_time
            log.error(
                f"[MCPClient.call_tool] Timeout | server={self.server_name} | "
                f"tool={tool_name} | timeout={self.timeout}s | elapsed={execution_time:.3f}s"
            )
            return {
                "success": False,
                "error": f"Timeout after {self.timeout}s",
                "executionTime": self.timeout,
            }

        except httpx.HTTPStatusError as e:
            execution_time = time.time() - start_time
            log.error(
                f"[MCPClient.call_tool] HTTP error | server={self.server_name} | "
                f"tool={tool_name} | status_code={e.response.status_code} | "
                f"elapsed={execution_time:.3f}s | response_text={e.response.text[:200]}"
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                "executionTime": 0,
            }

        except Exception as e:  # noqa: BLE001
            execution_time = time.time() - start_time
            log.error(
                f"[MCPClient.call_tool] Unexpected error | server={self.server_name} | "
                f"tool={tool_name} | elapsed={execution_time:.3f}s | "
                f"error_type={type(e).__name__} | error={str(e)[:200]}"
            )
            return {"success": False, "error": str(e), "executionTime": 0}

    async def close(self):
        self._mcp_session_id = None
        await self.client.aclose()
