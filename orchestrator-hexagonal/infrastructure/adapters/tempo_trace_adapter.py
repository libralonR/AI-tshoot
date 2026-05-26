"""Adapter Tempo → TraceSource (TraceQL).

Encapsula chamadas TraceQL ao MCP Tempo (que usa JSON-RPC nativo).
"""

import logging
from typing import Any, Dict

from infrastructure.mcp_client import MCPClient

log = logging.getLogger("orchestrator")


class TempoTraceAdapter:
    """TraceSource implementation backed by Grafana Tempo MCP (JSON-RPC)."""

    def __init__(self, mcp_client: MCPClient):
        self.mcp = mcp_client

    async def query_traces(
        self,
        query: str,
        limit: int = 20,
        start: str = "",
        end: str = "",
    ) -> Dict[str, Any]:
        args: Dict[str, Any] = {"query": query, "limit": limit}
        if start:
            args["start"] = start
        if end:
            args["end"] = end
        return await self.mcp.call_tool("traceql-search", args)

    async def get_trace(self, trace_id: str) -> Dict[str, Any]:
        return await self.mcp.call_tool("get-trace", {"trace_id": trace_id})
