#!/usr/bin/env python3
"""
VictoriaMetrics MCP Server (Python)

MCP server que consulta a API HTTP do VictoriaMetrics diretamente.
Mesmo padrão do Grafana MCP e Incidents PG MCP: REST /tools/{name} + SSE.
Read-only por design.

API VictoriaMetrics: https://docs.victoriametrics.com/url-examples/
"""

import asyncio
import json
import logging
import os
import sys
import time
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
log = logging.getLogger("victoriametrics-mcp")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
VM_URL = os.getenv("VM_URL", "http://localhost:8428").rstrip("/")
VM_BEARER_TOKEN = os.getenv("VM_BEARER_TOKEN", "")
VM_TIMEOUT = float(os.getenv("VM_TIMEOUT", "30"))
MCP_LISTEN_PORT = int(os.getenv("MCP_LISTEN_PORT", "8085"))


# ---------------------------------------------------------------------------
# VictoriaMetrics HTTP Client
# ---------------------------------------------------------------------------
class VMClient:
    """Client HTTP para a API do VictoriaMetrics."""

    def __init__(self, base_url: str, token: str = "", timeout: float = 30):
        self.base_url = base_url
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            verify=False,
            timeout=httpx.Timeout(timeout),
        )

    async def close(self):
        await self._http.aclose()

    async def get(self, path: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        log.debug(f"[VMClient] GET {path} | params={params}")
        resp = await self._http.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    # --- API Methods ---

    async def query(self, expr: str, time_param: str = None, step: str = None, timeout: str = None) -> dict:
        """Instant query: /api/v1/query"""
        params: Dict[str, str] = {"query": expr}
        if time_param:
            params["time"] = time_param
        if step:
            params["step"] = step
        if timeout:
            params["timeout"] = timeout
        return await self.get("/api/v1/query", params)

    async def query_range(self, expr: str, start: str, end: str = None, step: str = "1m", timeout: str = None) -> dict:
        """Range query: /api/v1/query_range"""
        params: Dict[str, str] = {"query": expr, "start": start, "step": step}
        if end:
            params["end"] = end
        if timeout:
            params["timeout"] = timeout
        return await self.get("/api/v1/query_range", params)

    async def series(self, match: str = None, start: str = None, end: str = None, limit: int = None) -> dict:
        """List series: /api/v1/series"""
        params: Dict[str, str] = {}
        if match:
            params["match[]"] = match
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        if limit:
            params["limit"] = str(limit)
        return await self.get("/api/v1/series", params)

    async def labels(self, match: str = None) -> dict:
        """List labels: /api/v1/labels"""
        params: Dict[str, str] = {}
        if match:
            params["match[]"] = match
        return await self.get("/api/v1/labels", params)

    async def label_values(self, label: str, match: str = None) -> dict:
        """Label values: /api/v1/label/{name}/values"""
        params: Dict[str, str] = {}
        if match:
            params["match[]"] = match
        return await self.get(f"/api/v1/label/{label}/values", params)

    async def tsdb_status(self, top_n: int = 10, date: str = None) -> dict:
        """TSDB status: /api/v1/status/tsdb"""
        params: Dict[str, str] = {"topN": str(top_n)}
        if date:
            params["date"] = date
        return await self.get("/api/v1/status/tsdb", params)

    async def rules(self) -> dict:
        """Alerting/recording rules: /api/v1/rules"""
        return await self.get("/api/v1/rules")

    async def alerts(self) -> dict:
        """Firing alerts: /api/v1/alerts"""
        return await self.get("/api/v1/alerts")

    async def metrics(self, match: str = None, limit: int = None) -> dict:
        """List metric names: /api/v1/label/__name__/values"""
        params: Dict[str, str] = {}
        if match:
            params["match[]"] = match
        if limit:
            params["limit"] = str(limit)
        return await self.get("/api/v1/label/__name__/values", params)


# ---------------------------------------------------------------------------
# Tool definitions (mesma interface que os outros MCPs)
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "query",
        "description": "Execute instant PromQL/MetricsQL query against VictoriaMetrics",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "PromQL or MetricsQL expression"},
                "time": {"type": "string", "description": "Evaluation timestamp (ISO 8601 or epoch). Default: now"},
                "step": {"type": "string", "description": "Lookback interval (e.g. 5m). Default: 5m"},
                "timeout": {"type": "string", "description": "Query timeout (e.g. 30s)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_range",
        "description": "Execute range PromQL/MetricsQL query over a time period",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "PromQL or MetricsQL expression"},
                "start": {"type": "string", "description": "Start timestamp (ISO 8601 or epoch)"},
                "end": {"type": "string", "description": "End timestamp. Default: now"},
                "step": {"type": "string", "description": "Resolution step (e.g. 1m, 5m). Default: 1m"},
                "timeout": {"type": "string", "description": "Query timeout"},
            },
            "required": ["query", "start"],
        },
    },
    {
        "name": "metrics",
        "description": "List available metric names in VictoriaMetrics",
        "inputSchema": {
            "type": "object",
            "properties": {
                "match": {"type": "string", "description": "Series selector to filter (e.g. {job='prometheus'})"},
                "limit": {"type": "integer", "description": "Max results"},
            },
        },
    },
    {
        "name": "labels",
        "description": "List available label names",
        "inputSchema": {
            "type": "object",
            "properties": {
                "match": {"type": "string", "description": "Series selector to filter"},
            },
        },
    },
    {
        "name": "label_values",
        "description": "List values for a specific label",
        "inputSchema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Label name (e.g. job, __name__)"},
                "match": {"type": "string", "description": "Series selector to filter"},
            },
            "required": ["label"],
        },
    },
    {
        "name": "series",
        "description": "List time series matching a selector",
        "inputSchema": {
            "type": "object",
            "properties": {
                "match": {"type": "string", "description": "Series selector (e.g. {application_service='grafana-tempo'})"},
                "start": {"type": "string", "description": "Start timestamp"},
                "end": {"type": "string", "description": "End timestamp"},
                "limit": {"type": "integer", "description": "Max results"},
            },
        },
    },
    {
        "name": "tsdb_status",
        "description": "TSDB cardinality statistics (top series, labels, label values)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topN": {"type": "integer", "description": "Number of top entries. Default: 10"},
                "date": {"type": "string", "description": "Date for stats (YYYY-MM-DD). Default: today"},
            },
        },
    },
    {
        "name": "alerts",
        "description": "View current firing and pending alerts from VictoriaMetrics/vmalert",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "rules",
        "description": "View alerting and recording rules",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ---------------------------------------------------------------------------
# Tool executor
# ---------------------------------------------------------------------------
async def execute_tool(client: VMClient, name: str, arguments: dict) -> dict:
    """Executa uma tool e retorna resultado normalizado."""
    start_time = time.time()

    try:
        if name == "query":
            result = await client.query(
                expr=arguments["query"],
                time_param=arguments.get("time"),
                step=arguments.get("step"),
                timeout=arguments.get("timeout"),
            )
        elif name == "query_range":
            result = await client.query_range(
                expr=arguments["query"],
                start=arguments["start"],
                end=arguments.get("end"),
                step=arguments.get("step", "1m"),
                timeout=arguments.get("timeout"),
            )
        elif name == "metrics":
            result = await client.metrics(
                match=arguments.get("match"),
                limit=arguments.get("limit"),
            )
        elif name == "labels":
            result = await client.labels(match=arguments.get("match"))
        elif name == "label_values":
            result = await client.label_values(
                label=arguments["label"],
                match=arguments.get("match"),
            )
        elif name == "series":
            result = await client.series(
                match=arguments.get("match"),
                start=arguments.get("start"),
                end=arguments.get("end"),
                limit=arguments.get("limit"),
            )
        elif name == "tsdb_status":
            result = await client.tsdb_status(
                top_n=arguments.get("topN", 10),
                date=arguments.get("date"),
            )
        elif name == "alerts":
            result = await client.alerts()
        elif name == "rules":
            result = await client.rules()
        else:
            return {"success": False, "error": f"Unknown tool: {name}"}

        elapsed = time.time() - start_time
        log.info(f"[execute_tool] {name} OK | time={elapsed:.3f}s")

        return {
            "success": True,
            "result": result.get("data", result),
            "status": result.get("status", "success"),
            "executionTime": elapsed,
        }

    except httpx.HTTPStatusError as e:
        elapsed = time.time() - start_time
        log.error(f"[execute_tool] {name} HTTP error | status={e.response.status_code} | time={elapsed:.3f}s")
        return {
            "success": False,
            "error": f"VictoriaMetrics returned {e.response.status_code}: {e.response.text[:200]}",
            "executionTime": elapsed,
        }
    except Exception as e:
        elapsed = time.time() - start_time
        log.error(f"[execute_tool] {name} error | {type(e).__name__}: {e} | time={elapsed:.3f}s")
        return {
            "success": False,
            "error": f"{type(e).__name__}: {str(e)}",
            "executionTime": elapsed,
        }


# ---------------------------------------------------------------------------
# REST Handlers
# ---------------------------------------------------------------------------
_client: Optional[VMClient] = None


def get_client() -> VMClient:
    global _client
    if _client is None:
        _client = VMClient(VM_URL, VM_BEARER_TOKEN, VM_TIMEOUT)
        log.info(f"[get_client] Connected to VictoriaMetrics: {VM_URL}")
    return _client


async def handle_health(request: Request):
    try:
        client = get_client()
        # VictoriaMetrics health check
        result = await client.get("/health")
        return JSONResponse({"status": "ok", "upstream": VM_URL})
    except Exception:
        # /health pode não retornar JSON, tentar raw
        try:
            async with httpx.AsyncClient(verify=False, timeout=5) as http:
                resp = await http.get(f"{VM_URL}/health")
                ok = resp.status_code == 200
            return JSONResponse(
                {"status": "ok" if ok else "degraded", "upstream": VM_URL},
                status_code=200 if ok else 503,
            )
        except Exception:
            return JSONResponse({"status": "degraded", "upstream": VM_URL}, status_code=503)


async def handle_list_tools(request: Request):
    return JSONResponse({"tools": TOOLS})


async def handle_tool_call(request: Request):
    tool_name = request.path_params["tool_name"]
    body = await request.json()
    arguments = body.get("arguments", {})

    log.info(f"[handle_tool_call] /tools/{tool_name} | args={json.dumps(arguments)[:200]}")

    client = get_client()
    result = await execute_tool(client, tool_name, arguments)

    status_code = 200 if result.get("success", False) else 500
    return JSONResponse(result, status_code=status_code)


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
    log.info(f"Starting VictoriaMetrics MCP Server (Python) on port {MCP_LISTEN_PORT}")
    log.info(f"VictoriaMetrics URL: {VM_URL}")
    log.info(f"Bearer token: {'set' if VM_BEARER_TOKEN else 'not set'}")
    log.info(f"Timeout: {VM_TIMEOUT}s")
    uvicorn.run(app, host="0.0.0.0", port=MCP_LISTEN_PORT, log_level="info")
