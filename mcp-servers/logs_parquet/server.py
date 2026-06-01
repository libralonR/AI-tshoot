"""
MCP Server instance, list_tools, call_tool, and main entry points (stdio/sse).
"""

import asyncio
import json
import os
import time

from mcp.server import Server
from mcp.server.stdio import stdio_server

from .config import DEFAULT_LIMIT, MAX_LIMIT, MAX_WINDOW_HOURS, MAX_PARTITIONS, log
from .tools import (
    _ensure_initialized,
    search_logs,
    count_logs_by_level,
    find_error_patterns,
    get_logs_by_trace_id,
    get_log_volume_timeline,
    list_capabilities,
)
from . import tools as _tools_mod

# ---------------------------------------------------------------------------
# MCP server (stdio + REST)
# ---------------------------------------------------------------------------

app = Server("logs-parquet-mcp")


@app.list_tools()
async def list_tools() -> list:
    return [
        {
            "name": "search_logs",
            "description": (
                "Busca livre em logs forenses (S3 Parquet). Filtros opcionais por "
                "application_service, business_capability, level, text_match. "
                "Janela start/end obrigatória (max 24h)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "application_service": {"type": "string"},
                    "business_capability": {
                        "type": "string",
                        "description": "Capability do path (ex: acquirer-c6pay). Pode ser CSV.",
                    },
                    "level": {"type": "string", "description": "ERROR, WARN, INFO, DEBUG"},
                    "text_match": {"type": "string", "description": "ILIKE %text%"},
                    "start": {"type": "string", "description": "ISO 8601 ou epoch_ms"},
                    "end": {"type": "string", "description": "ISO 8601 ou epoch_ms (default: agora)"},
                    "limit": {"type": "integer", "description": f"Default {DEFAULT_LIMIT}, max {MAX_LIMIT}"},
                },
                "required": ["start"],
            },
        },
        {
            "name": "count_logs_by_level",
            "description": "Contagem agregada de logs por level na janela.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "application_service": {"type": "string"},
                    "business_capability": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                },
                "required": ["start"],
            },
        },
        {
            "name": "find_error_patterns",
            "description": (
                "Top error patterns por frequência (level=ERROR). Mensagens são "
                "normalizadas (números, UUIDs, strings) para agrupar variantes."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "application_service": {"type": "string"},
                    "business_capability": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "top_n": {"type": "integer"},
                },
                "required": ["application_service", "start"],
            },
        },
        {
            "name": "get_logs_by_trace_id",
            "description": (
                "Recupera logs cujo conteúdo (em args, extra-fields ou message) "
                "contém o trace_id. Janela default: última 1h."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "trace_id": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "business_capability": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["trace_id"],
            },
        },
        {
            "name": "get_log_volume_timeline",
            "description": "Timeline de volume de logs agrupado por bucket de tempo e level.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "application_service": {"type": "string"},
                    "business_capability": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "step": {
                        "type": "string",
                        "description": "1m | 5m | 15m | 1h | 6h | 1d (default 1h)",
                    },
                },
                "required": ["start"],
            },
        },
        {
            "name": "list_capabilities",
            "description": "Lista valores capability=<bcap> presentes no bucket S3.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


_TOOL_DISPATCH = {
    "search_logs": search_logs,
    "count_logs_by_level": count_logs_by_level,
    "find_error_patterns": find_error_patterns,
    "get_logs_by_trace_id": get_logs_by_trace_id,
    "get_log_volume_timeline": get_log_volume_timeline,
    "list_capabilities": list_capabilities,
}


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list:
    log.info(f"Tool called: {name} with arguments: {arguments}")
    start_time = time.time()
    try:
        fn = _TOOL_DISPATCH.get(name)
        if fn is None:
            raise ValueError(f"Unknown tool: {name}")
        # Tools são síncronas (DuckDB) — rodar em threadpool para não bloquear
        # o event loop quando chamadas via MCP/SSE.
        result = await asyncio.to_thread(fn, **arguments)
        result.setdefault("executionTime", time.time() - start_time)
        return [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]
    except Exception as e:  # noqa: BLE001
        log.exception(f"Tool {name} failed")
        error_result = {
            "success": False,
            "error": str(e),
            "executionTime": time.time() - start_time,
        }
        return [{"type": "text", "text": json.dumps(error_result, indent=2)}]


# ---------------------------------------------------------------------------
# Modes: stdio vs sse (REST)
# ---------------------------------------------------------------------------

async def main_stdio():
    log.info("Starting Logs Parquet MCP Server in stdio mode")
    _ensure_initialized()
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


def main_sse():
    """Run in SSE mode with REST endpoints (Docker / K8s)."""
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route
    from mcp.server.sse import SseServerTransport
    import uvicorn

    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await app.run(streams[0], streams[1], app.create_initialization_options())

    async def handle_health(request):
        try:
            _ensure_initialized()
            _config = _tools_mod._config
            return JSONResponse({
                "status": "healthy",
                "service": "logs-parquet-mcp",
                "backend": "duckdb",
                "bucket": _config.bucket if _config else None,
                "region": _config.aws_region if _config else None,
                "role_arn": _config.role_arn if _config else None,
                "max_window_hours": MAX_WINDOW_HOURS,
                "max_partitions": MAX_PARTITIONS,
            })
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {"status": "unhealthy", "error": str(exc)}, status_code=503
            )

    async def handle_list_tools_endpoint(request):
        tools = await list_tools()
        return JSONResponse({"tools": tools})

    async def handle_tool_call(request: Request):
        tool_name = request.path_params["tool_name"]
        body = await request.json()
        arguments = body.get("arguments", {})
        log.info(f"REST /tools/{tool_name} called with: {arguments}")
        try:
            result = await call_tool(tool_name, arguments)
            text = result[0]["text"] if result else "{}"
            parsed = json.loads(text)
            log.info(
                f"REST /tools/{tool_name} success={parsed.get('success')} "
                f"executionTime={parsed.get('executionTime'):.3f}s"
            )
            status = 200 if parsed.get("success") else 500
            return JSONResponse(parsed, status_code=status)
        except Exception as e:  # noqa: BLE001
            log.exception(f"REST /tools/{tool_name} error: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    starlette_app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
            Route("/health", endpoint=handle_health),
            Route("/tools", endpoint=handle_list_tools_endpoint),
            Route("/tools/{tool_name}", endpoint=handle_tool_call, methods=["POST"]),
        ]
    )

    port = int(os.getenv("MCP_LISTEN_PORT", "8080"))
    log.info(f"Starting Logs Parquet MCP Server in SSE/REST mode on port {port}")
    _ensure_initialized()
    uvicorn.run(starlette_app, host="0.0.0.0", port=port, log_level="info")
