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
        self.endpoint = endpoint
        self.timeout = timeout
        # verify=False para ambientes corporativos com proxy/certificados internos
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            verify=False
        )

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
            response = await self.client.post(
                f"{self.endpoint}/tools/{tool_name}",
                json={"arguments": arguments},
            )
            response.raise_for_status()
            result = response.json()
            
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
        await self.client.aclose()
