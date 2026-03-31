"""HTTP client for communicating with MCP servers."""

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
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(timeout), verify=False)

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            log.info(f"Calling {self.server_name}.{tool_name} with args: {arguments}")
            response = await self.client.post(
                f"{self.endpoint}/tools/{tool_name}",
                json={"arguments": arguments},
            )
            response.raise_for_status()
            result = response.json()
            log.info(f"{self.server_name}.{tool_name} completed in {result.get('executionTime', 0)}s")
            return result
        except httpx.TimeoutException:
            log.error(f"Timeout calling {self.server_name}.{tool_name}")
            return {"success": False, "error": f"Timeout after {self.timeout}s", "executionTime": self.timeout}
        except httpx.HTTPStatusError as e:
            log.error(f"HTTP error calling {self.server_name}.{tool_name}: {e}")
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}", "executionTime": 0}
        except Exception as e:
            log.error(f"Error calling {self.server_name}.{tool_name}: {e}")
            return {"success": False, "error": str(e), "executionTime": 0}

    async def close(self):
        await self.client.aclose()
