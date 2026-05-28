#!/usr/bin/env python3
"""
Splunk MCP Server
Executes SPL queries against Splunk via REST API (oneshot mode).
Read-only by design (guardrail).
Uses httpx.AsyncClient for HTTP calls.
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server

# Logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("splunk-mcp")


# ============================================================================
# Configuration
# ============================================================================

SPLUNK_URL = os.getenv("SPLUNK_URL", "https://localhost:8089")
SPLUNK_TOKEN = os.getenv("SPLUNK_TOKEN", "")
SPLUNK_AUTH_SCHEME = os.getenv("SPLUNK_AUTH_SCHEME", "Bearer")
SPLUNK_INSECURE_SKIP_VERIFY = os.getenv("SPLUNK_INSECURE_SKIP_VERIFY", "true").lower() in ("true", "1", "yes")
SPLUNK_TIMEOUT_S = int(os.getenv("SPLUNK_TIMEOUT_S", "60"))


# ============================================================================
# PII Redaction (inline, same patterns as orchestrator/guardrails.py)
# ============================================================================

def redact_pii(text: str) -> tuple[str, bool]:
    """Redact PII from text. Returns (redacted_text, was_redacted)."""
    redacted = False

    if re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text):
        text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL_REDACTED]', text)
        redacted = True

    if re.search(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', text):
        text = re.sub(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', '[PHONE_REDACTED]', text)
        redacted = True

    if re.search(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', text):
        text = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[IP_REDACTED]', text)
        redacted = True

    if re.search(r'\b(?:sk-|glsa_|xox[bpas]-|ghp_|gho_|AKIA)[A-Za-z0-9_\-]{20,}\b', text):
        text = re.sub(r'\b(?:sk-|glsa_|xox[bpas]-|ghp_|gho_|AKIA)[A-Za-z0-9_\-]{20,}\b', '[API_KEY_REDACTED]', text)
        redacted = True

    return text, redacted


# ============================================================================
# Splunk HTTP Client
# ============================================================================

_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    """Get or create the shared httpx.AsyncClient for Splunk API calls."""
    global _http_client
    if _http_client is None:
        verify = not SPLUNK_INSECURE_SKIP_VERIFY
        _http_client = httpx.AsyncClient(
            verify=verify,
            timeout=httpx.Timeout(timeout=SPLUNK_TIMEOUT_S, connect=10.0),
            headers={
                "Authorization": f"{SPLUNK_AUTH_SCHEME} {SPLUNK_TOKEN}",
            },
        )
        log.info(
            f"Created httpx client | url={SPLUNK_URL} | "
            f"auth_scheme={SPLUNK_AUTH_SCHEME} | "
            f"verify_ssl={verify} | "
            f"timeout={SPLUNK_TIMEOUT_S}s"
        )
    return _http_client


async def _splunk_oneshot(spl: str, earliest_time: str = "-1h", latest_time: str = "now", max_count: int = 100) -> Dict[str, Any]:
    """Execute a Splunk oneshot search and return results.

    Uses POST {SPLUNK_URL}/services/search/jobs/oneshot?output_mode=json
    """
    client = _get_http_client()
    url = f"{SPLUNK_URL}/services/search/jobs/oneshot"

    params = {"output_mode": "json"}
    data = {
        "search": spl,
        "earliest_time": earliest_time,
        "latest_time": latest_time,
        "max_count": str(max_count),
    }

    log.info(
        f"[_splunk_oneshot] Executing SPL | "
        f"query={spl[:200]}{'...' if len(spl) > 200 else ''} | "
        f"earliest_time={earliest_time} | "
        f"latest_time={latest_time} | "
        f"max_count={max_count}"
    )

    response = await client.post(url, params=params, data=data)
    response.raise_for_status()

    result = response.json()
    results_list = result.get("results", [])
    log.info(f"[_splunk_oneshot] Returned {len(results_list)} results")
    return result


# ============================================================================
# Tool Implementations
# ============================================================================

async def _tool_search(arguments: dict) -> dict:
    """Run arbitrary SPL query."""
    query = arguments.get("query")
    if not query:
        return {"success": False, "error": "Missing required argument: query"}

    earliest_time = arguments.get("earliest_time", "-1h")
    latest_time = arguments.get("latest_time", "now")
    max_results = min(int(arguments.get("max_results", 100)), 10000)

    # Ensure query starts with 'search' or '|' for Splunk API
    spl = query.strip()
    if not spl.startswith("|") and not spl.lower().startswith("search "):
        spl = f"search {spl}"

    start_time = time.time()
    try:
        result = await _splunk_oneshot(spl, earliest_time, latest_time, max_results)
        execution_time = time.time() - start_time

        results_list = result.get("results", [])

        # PII redaction on results
        results_str = json.dumps(results_list, default=str)
        results_str, pii_redacted = redact_pii(results_str)
        results_list = json.loads(results_str)

        if pii_redacted:
            log.info(f"[search] PII redacted from results")

        return {
            "success": True,
            "result": results_list,
            "count": len(results_list),
            "executionTime": execution_time,
            "query": spl,
        }
    except httpx.HTTPStatusError as e:
        execution_time = time.time() - start_time
        log.error(f"[search] HTTP error: {e.response.status_code} - {e.response.text[:300]}")
        return {
            "success": False,
            "error": f"Splunk API error: {e.response.status_code} - {e.response.text[:200]}",
            "executionTime": execution_time,
        }
    except Exception as e:
        execution_time = time.time() - start_time
        log.exception(f"[search] Error executing query")
        return {
            "success": False,
            "error": f"Error: {type(e).__name__}: {str(e)}",
            "executionTime": execution_time,
        }


async def _tool_errors(arguments: dict) -> dict:
    """Find top error patterns for a service."""
    application_service = arguments.get("application_service")
    if not application_service:
        return {"success": False, "error": "Missing required argument: application_service"}

    earliest_time = arguments.get("earliest_time", "-1h")
    latest_time = arguments.get("latest_time", "now")
    top_n = int(arguments.get("top_n", 10))

    # SPL: filter by service, find errors, extract patterns, top N
    spl = (
        f'search index=* application_service="{application_service}" '
        f'(level=ERROR OR level=error OR loglevel=ERROR OR severity=error OR log_level=ERROR) '
        f'| rex field=_raw "(?i)(?:error|exception|fail(?:ed|ure)?)[:\\s]+(?<error_message>[^\\n]{{1,200}})" '
        f'| stats count as occurrences by error_message '
        f'| sort -occurrences '
        f'| head {top_n}'
    )

    start_time = time.time()
    try:
        result = await _splunk_oneshot(spl, earliest_time, latest_time, max_count=top_n)
        execution_time = time.time() - start_time

        results_list = result.get("results", [])

        # PII redaction
        results_str = json.dumps(results_list, default=str)
        results_str, pii_redacted = redact_pii(results_str)
        results_list = json.loads(results_str)

        return {
            "success": True,
            "result": results_list,
            "count": len(results_list),
            "executionTime": execution_time,
            "application_service": application_service,
            "query": spl,
        }
    except httpx.HTTPStatusError as e:
        execution_time = time.time() - start_time
        log.error(f"[errors] HTTP error: {e.response.status_code} - {e.response.text[:300]}")
        return {
            "success": False,
            "error": f"Splunk API error: {e.response.status_code} - {e.response.text[:200]}",
            "executionTime": execution_time,
        }
    except Exception as e:
        execution_time = time.time() - start_time
        log.exception(f"[errors] Error executing query")
        return {
            "success": False,
            "error": f"Error: {type(e).__name__}: {str(e)}",
            "executionTime": execution_time,
        }


async def _tool_patterns(arguments: dict) -> dict:
    """Find log patterns using Splunk cluster command."""
    application_service = arguments.get("application_service")
    if not application_service:
        return {"success": False, "error": "Missing required argument: application_service"}

    earliest_time = arguments.get("earliest_time", "-1h")
    latest_time = arguments.get("latest_time", "now")

    # SPL: filter by service, cluster logs to find patterns
    spl = (
        f'search index=* application_service="{application_service}" '
        f'| cluster showcount=true '
        f'| table cluster_count, _raw '
        f'| sort -cluster_count '
        f'| head 50'
    )

    start_time = time.time()
    try:
        result = await _splunk_oneshot(spl, earliest_time, latest_time, max_count=50)
        execution_time = time.time() - start_time

        results_list = result.get("results", [])

        # PII redaction
        results_str = json.dumps(results_list, default=str)
        results_str, pii_redacted = redact_pii(results_str)
        results_list = json.loads(results_str)

        return {
            "success": True,
            "result": results_list,
            "count": len(results_list),
            "executionTime": execution_time,
            "application_service": application_service,
            "query": spl,
        }
    except httpx.HTTPStatusError as e:
        execution_time = time.time() - start_time
        log.error(f"[patterns] HTTP error: {e.response.status_code} - {e.response.text[:300]}")
        return {
            "success": False,
            "error": f"Splunk API error: {e.response.status_code} - {e.response.text[:200]}",
            "executionTime": execution_time,
        }
    except Exception as e:
        execution_time = time.time() - start_time
        log.exception(f"[patterns] Error executing query")
        return {
            "success": False,
            "error": f"Error: {type(e).__name__}: {str(e)}",
            "executionTime": execution_time,
        }


# ============================================================================
# MCP Server
# ============================================================================

app = Server("splunk-mcp")


@app.list_tools()
async def list_tools() -> list[dict]:
    return [
        {
            "name": "search",
            "description": "Execute an arbitrary SPL query against Splunk.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "SPL query string",
                    },
                    "earliest_time": {
                        "type": "string",
                        "description": "Start time (default: -1h). Supports Splunk time modifiers.",
                    },
                    "latest_time": {
                        "type": "string",
                        "description": "End time (default: now). Supports Splunk time modifiers.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 100, max: 10000)",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "errors",
            "description": "Find top error patterns for a service in Splunk logs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "application_service": {
                        "type": "string",
                        "description": "Service/component name (application_service label)",
                    },
                    "earliest_time": {
                        "type": "string",
                        "description": "Start time (default: -1h)",
                    },
                    "latest_time": {
                        "type": "string",
                        "description": "End time (default: now)",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "Number of top error patterns to return (default: 10)",
                    },
                },
                "required": ["application_service"],
            },
        },
        {
            "name": "patterns",
            "description": "Find log patterns using Splunk cluster command for a service.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "application_service": {
                        "type": "string",
                        "description": "Service/component name (application_service label)",
                    },
                    "earliest_time": {
                        "type": "string",
                        "description": "Start time (default: -1h)",
                    },
                    "latest_time": {
                        "type": "string",
                        "description": "End time (default: now)",
                    },
                },
                "required": ["application_service"],
            },
        },
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[dict]:
    log.info(f"Tool called: {name} with arguments: {arguments}")
    start_time = time.time()

    try:
        if name == "search":
            result = await _tool_search(arguments)
        elif name == "errors":
            result = await _tool_errors(arguments)
        elif name == "patterns":
            result = await _tool_patterns(arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")

        return [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]

    except Exception as e:
        log.exception(f"Error in tool {name}")
        error_result = {
            "success": False,
            "error": str(e),
            "executionTime": time.time() - start_time,
        }
        return [{"type": "text", "text": json.dumps(error_result, indent=2)}]


# ============================================================================
# Server Modes (SSE + stdio)
# ============================================================================

async def main_stdio():
    """Run in stdio mode (for Kiro local / CLI)."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


def main_sse():
    """Run in SSE mode with REST endpoints (for Docker / K8s)."""
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route, Mount
    from mcp.server.sse import SseServerTransport
    import uvicorn

    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await app.run(
                streams[0], streams[1], app.create_initialization_options()
            )

    async def handle_health(request):
        return JSONResponse({"status": "ok", "service": "splunk-mcp"})

    async def handle_tool_call(request: Request):
        tool_name = request.path_params["tool_name"]
        body = await request.json()
        arguments = body.get("arguments", {})
        log.info(f"REST /tools/{tool_name} called with: {arguments}")
        try:
            result = await call_tool(tool_name, arguments)
            text = result[0]["text"] if result else "{}"
            parsed = json.loads(text)
            log.info(f"REST /tools/{tool_name} success={parsed.get('success')}")
            return JSONResponse(parsed)
        except Exception as e:
            log.exception(f"REST /tools/{tool_name} error: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    async def handle_list_tools_endpoint(request):
        tools = await list_tools()
        return JSONResponse({"tools": tools})

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
    log.info(f"Starting Splunk MCP Server in SSE mode on port {port}")
    uvicorn.run(starlette_app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    mode = os.getenv("MCP_SERVER_MODE", "sse").lower()
    log.info(f"Starting splunk.py in mode={mode}")
    log.info(f"ENV: SPLUNK_URL={os.getenv('SPLUNK_URL', 'NOT SET')}")
    log.info(f"ENV: SPLUNK_AUTH_SCHEME={SPLUNK_AUTH_SCHEME}")
    log.info(f"ENV: SPLUNK_INSECURE_SKIP_VERIFY={SPLUNK_INSECURE_SKIP_VERIFY}")
    log.info(f"ENV: SPLUNK_TIMEOUT_S={SPLUNK_TIMEOUT_S}")
    log.info(f"ENV: MCP_SERVER_MODE={mode}")
    log.info(f"ENV: MCP_LISTEN_PORT={os.getenv('MCP_LISTEN_PORT', '8080')}")
    if mode == "sse":
        main_sse()
    else:
        asyncio.run(main_stdio())
