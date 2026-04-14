"""LLM client for conversational investigation using OpenAI."""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI
from pathlib import Path

from metrics import LLM_CALL_DURATION, LLM_CALL_TOTAL, LLM_TOKENS, LLM_TOOL_CALLS

log = logging.getLogger("orchestrator")

PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_system_prompt() -> str:
    """Load orchestrator system prompt from file."""
    prompt_file = PROMPTS_DIR / "orchestrator-prompt.md"
    if prompt_file.exists():
        return prompt_file.read_text()
    return "You are an observability troubleshooting copilot for SRE teams."


# Tools that the LLM can call (function calling)
AVAILABLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_firing_alerts",
            "description": "Find currently firing alerts in Grafana. Use when user asks about alerts, incidents, or problems.",
            "parameters": {
                "type": "object",
                "properties": {
                    "application_service": {
                        "type": "string",
                        "description": "Filter by service/component name",
                    },
                    "owner_squad": {
                        "type": "string",
                        "description": "Filter by responsible squad",
                    },
                    "severidade": {
                        "type": "string",
                        "description": "Filter by severity: P1, P2, P3",
                    },
                    "business_capability": {
                        "type": "string",
                        "description": "Filter by business capability",
                    },
                    "alertname": {
                        "type": "string",
                        "description": "Filter by alert rule name",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_alert_details",
            "description": "Get details of a specific Grafana alert by its UID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "alertUID": {
                        "type": "string",
                        "description": "The Grafana alert rule UID",
                    },
                },
                "required": ["alertUID"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_incident",
            "description": "Get incident details by number (e.g. INC0012345).",
            "parameters": {
                "type": "object",
                "properties": {
                    "number": {
                        "type": "string",
                        "description": "Incident number (e.g. INC0012345)",
                    },
                },
                "required": ["number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_incidents",
            "description": "Search incidents by filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "application_service": {
                        "type": "string",
                        "description": "Service/component name",
                    },
                    "priority": {
                        "type": "string",
                        "description": "Priority: 1, 2, 3, 4",
                    },
                    "state": {
                        "type": "string",
                        "description": "State: New, In Progress, Resolved, Closed",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_dashboards",
            "description": "Find Grafana dashboards by labels or tags.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Dashboard tags to filter by",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_related_incidents",
            "description": "Find incidents related to a specific incident or service. Supports filtering by multiple Grafana labels in the description field.",
            "parameters": {
                "type": "object",
                "properties": {
                    "number": {
                        "type": "string",
                        "description": "Reference incident number (e.g. INC0012345)",
                    },
                    "application_service": {
                        "type": "string",
                        "description": "Service name to find related incidents",
                    },
                    "business_capability": {
                        "type": "string",
                        "description": "Business capability to filter incidents (searches Grafana labels in description)",
                    },
                    "business_domain": {
                        "type": "string",
                        "description": "Business domain to filter incidents",
                    },
                    "business_service": {
                        "type": "string",
                        "description": "Business service to filter incidents",
                    },
                    "owner_squad": {
                        "type": "string",
                        "description": "Owner squad to filter incidents",
                    },
                    "owner_sre": {
                        "type": "string",
                        "description": "Owner SRE to filter incidents",
                    },
                    "time_window_hours": {
                        "type": "integer",
                        "description": "Time window in hours (default: 24)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_incident_stats",
            "description": "Get incident statistics grouped by priority, category, state, or assignment_group.",
            "parameters": {
                "type": "object",
                "properties": {
                    "application_service": {
                        "type": "string",
                        "description": "Filter by service name",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Period in days (default: 30)",
                    },
                    "group_by": {
                        "type": "string",
                        "enum": ["priority", "category", "state", "assignment_group_name"],
                        "description": "Group results by this field",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_panel_link",
            "description": "Generate a direct link to a specific Grafana dashboard panel with time range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dashboardUID": {
                        "type": "string",
                        "description": "Dashboard UID",
                    },
                    "panelId": {
                        "type": "integer",
                        "description": "Panel ID",
                    },
                    "timeRange": {
                        "type": "object",
                        "properties": {
                            "start": {"type": "integer", "description": "Start time in epoch ms"},
                            "end": {"type": "integer", "description": "End time in epoch ms"},
                        },
                    },
                },
                "required": ["dashboardUID", "panelId"],
            },
        },
    },
    # ------------------------------------------------------------------
    # VictoriaMetrics MCP tools (via vm_mcp_proxy)
    # ------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "query",
            "description": "Execute an instant PromQL/MetricsQL query against VictoriaMetrics. Returns current metric values.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "PromQL or MetricsQL expression (e.g. 'up', 'rate(http_requests_total[5m])')",
                    },
                    "time": {
                        "type": "string",
                        "description": "Evaluation timestamp (ISO 8601 or epoch ms). Defaults to now.",
                    },
                    "step": {
                        "type": "string",
                        "description": "Lookback interval for raw samples (e.g. '5m'). Default: 5m.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_range",
            "description": "Execute a range PromQL/MetricsQL query over a time period. Returns time series data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "PromQL or MetricsQL expression",
                    },
                    "start": {
                        "type": "string",
                        "description": "Start timestamp (ISO 8601 or epoch ms)",
                    },
                    "end": {
                        "type": "string",
                        "description": "End timestamp (ISO 8601 or epoch ms). Defaults to now.",
                    },
                    "step": {
                        "type": "string",
                        "description": "Query resolution step (e.g. '1m', '5m', '1h')",
                    },
                },
                "required": ["query", "start"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "metrics",
            "description": "List available metric names in VictoriaMetrics. Optionally filter by pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "match": {
                        "type": "string",
                        "description": "Optional series selector to filter metrics (e.g. '{job=\"prometheus\"}')",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of metrics to return",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "labels",
            "description": "List available label names in VictoriaMetrics.",
            "parameters": {
                "type": "object",
                "properties": {
                    "match": {
                        "type": "string",
                        "description": "Optional series selector to filter labels",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "label_values",
            "description": "List values for a specific label in VictoriaMetrics.",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Label name to get values for (e.g. 'job', 'instance', '__name__')",
                    },
                    "match": {
                        "type": "string",
                        "description": "Optional series selector to filter",
                    },
                },
                "required": ["label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "alerts",
            "description": "View current alerts (firing and pending) from VictoriaMetrics/vmalert.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tsdb_status",
            "description": "View TSDB cardinality statistics from VictoriaMetrics. Shows top series, labels, and label values by cardinality.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topN": {
                        "type": "integer",
                        "description": "Number of top entries to return (default: 10)",
                    },
                    "date": {
                        "type": "string",
                        "description": "Date for cardinality stats (YYYY-MM-DD). Defaults to today.",
                    },
                },
            },
        },
    },
]


class LLMClient:
    """OpenAI-based LLM client with function calling for tool use."""

    def __init__(self, mcp_tools: Dict[str, Any] = None):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            log.error("[LLMClient.__init__] OPENAI_API_KEY env var not set")
            raise RuntimeError("OPENAI_API_KEY env var not set")

        # Configurar timeouts (em segundos)
        timeout = float(os.getenv("OPENAI_TIMEOUT", "60"))
        connect_timeout = float(os.getenv("OPENAI_CONNECT_TIMEOUT", "10"))
        
        # Skip SSL verification if behind corporate proxy
        import httpx as _httpx
        http_client = _httpx.AsyncClient(
            verify=False,
            timeout=_httpx.Timeout(
                timeout=timeout,
                connect=connect_timeout,
                read=timeout,
                write=10.0,
                pool=5.0
            )
        )

        base_url = os.getenv("OPENAI_BASE_URL")
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=http_client,
            max_retries=2,  # Reduzir retries para falhar mais rápido
        )
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o")
        self.system_prompt = _load_system_prompt()
        self.mcp_tools = mcp_tools or {}
        self.conversation_history: List[Dict[str, Any]] = []
        
        log.info(
            f"[LLMClient.__init__] Initialized | "
            f"model={self.model} | "
            f"base_url={base_url or 'default'} | "
            f"timeout={timeout}s | "
            f"connect_timeout={connect_timeout}s | "
            f"max_retries=2 | "
            f"system_prompt_length={len(self.system_prompt)} | "
            f"available_tools={len(AVAILABLE_TOOLS)}"
        )

    async def chat(
        self,
        user_message: str,
        tool_executor: Any = None,
    ) -> str:
        """Send a message and get a response, with automatic tool calling."""
        import time
        
        log.info(
            f"[LLMClient.chat] Starting chat | "
            f"user_message_length={len(user_message)} | "
            f"conversation_history_length={len(self.conversation_history)} | "
            f"tool_executor={'configured' if tool_executor else 'not_configured'}"
        )

        # Add system prompt on first message
        if not self.conversation_history:
            self.conversation_history.append(
                {"role": "system", "content": self.system_prompt}
            )
            log.debug(f"[LLMClient.chat] Added system prompt to conversation")

        # Add user message
        self.conversation_history.append({"role": "user", "content": user_message})
        log.debug(f"[LLMClient.chat] Added user message to conversation")

        # Call LLM with tools
        log.info(
            f"[LLMClient.chat] Calling OpenAI API | "
            f"model={self.model} | "
            f"messages_count={len(self.conversation_history)} | "
            f"tools_count={len(AVAILABLE_TOOLS)}"
        )
        
        try:
            start_time = time.time()
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=self.conversation_history,
                tools=AVAILABLE_TOOLS,
                tool_choice="auto",
                temperature=0.1,
            )
            api_time = time.time() - start_time
            
            log.info(
                f"[LLMClient.chat] OpenAI API response received | "
                f"api_time={api_time:.3f}s | "
                f"finish_reason={response.choices[0].finish_reason} | "
                f"usage={response.usage.model_dump() if response.usage else 'N/A'}"
            )
            # Record LLM metrics
            LLM_CALL_DURATION.labels(model=self.model, status="success").observe(api_time)
            LLM_CALL_TOTAL.labels(model=self.model, status="success").inc()
            if response.usage:
                LLM_TOKENS.labels(model=self.model, type="prompt_tokens").inc(response.usage.prompt_tokens or 0)
                LLM_TOKENS.labels(model=self.model, type="completion_tokens").inc(response.usage.completion_tokens or 0)
                LLM_TOKENS.labels(model=self.model, type="total_tokens").inc(response.usage.total_tokens or 0)
        except Exception as e:
            api_time = time.time() - start_time
            error_type = type(e).__name__
            error_msg = str(e)
            LLM_CALL_DURATION.labels(model=self.model, status="error").observe(api_time)
            LLM_CALL_TOTAL.labels(model=self.model, status="error").inc()
            
            # Identificar tipo específico de erro
            if "ConnectTimeout" in error_type or "ConnectError" in error_type:
                log.error(
                    f"[LLMClient.chat] Connection timeout/error | "
                    f"error_type={error_type} | "
                    f"elapsed={api_time:.3f}s | "
                    f"base_url={os.getenv('OPENAI_BASE_URL')} | "
                    f"error={error_msg[:200]}"
                )
                raise RuntimeError(
                    f"Não foi possível conectar ao LLM Gateway. "
                    f"Verifique conectividade de rede e DNS. "
                    f"URL: {os.getenv('OPENAI_BASE_URL')} | "
                    f"Erro: {error_msg[:100]}"
                ) from e
            elif "APITimeoutError" in error_type:
                log.error(
                    f"[LLMClient.chat] API timeout | "
                    f"error_type={error_type} | "
                    f"elapsed={api_time:.3f}s | "
                    f"timeout={os.getenv('OPENAI_TIMEOUT', '60')}s | "
                    f"error={error_msg[:200]}"
                )
                raise RuntimeError(
                    f"Timeout ao chamar LLM Gateway após {api_time:.1f}s. "
                    f"Aumente OPENAI_TIMEOUT (atual: {os.getenv('OPENAI_TIMEOUT', '60')}s) "
                    f"ou verifique performance do gateway."
                ) from e
            else:
                log.error(
                    f"[LLMClient.chat] OpenAI API call failed | "
                    f"error_type={error_type} | "
                    f"elapsed={api_time:.3f}s | "
                    f"error={error_msg[:200]}"
                )
                raise

        message = response.choices[0].message
        tool_call_iteration = 0

        # Handle tool calls (function calling)
        while message.tool_calls:
            tool_call_iteration += 1
            log.info(
                f"[LLMClient.chat] Processing tool calls | "
                f"iteration={tool_call_iteration} | "
                f"tool_calls_count={len(message.tool_calls)}"
            )
            
            self.conversation_history.append(message.model_dump())

            for idx, tool_call in enumerate(message.tool_calls, 1):
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments)

                log.info(
                    f"[LLMClient.chat] Executing tool call {idx}/{len(message.tool_calls)} | "
                    f"tool={fn_name} | "
                    f"args={fn_args} | "
                    f"tool_call_id={tool_call.id}"
                )

                # Execute tool via MCP
                tool_start = time.time()
                if tool_executor:
                    try:
                        tool_result = await tool_executor(fn_name, fn_args)
                        tool_time = time.time() - tool_start
                        LLM_TOOL_CALLS.labels(tool=fn_name, status="success").inc()
                        log.info(
                            f"[LLMClient.chat] Tool execution completed | "
                            f"tool={fn_name} | "
                            f"execution_time={tool_time:.3f}s | "
                            f"result_size={len(json.dumps(tool_result, default=str))} bytes"
                        )
                    except Exception as e:
                        tool_time = time.time() - tool_start
                        LLM_TOOL_CALLS.labels(tool=fn_name, status="error").inc()
                        log.error(
                            f"[LLMClient.chat] Tool execution failed | "
                            f"tool={fn_name} | "
                            f"execution_time={tool_time:.3f}s | "
                            f"error_type={type(e).__name__} | "
                            f"error={str(e)[:200]}"
                        )
                        tool_result = {"error": f"Tool execution failed: {str(e)}"}
                else:
                    tool_result = {"error": "No tool executor configured"}
                    log.warning(f"[LLMClient.chat] No tool executor configured for {fn_name}")

                # Add tool result to conversation
                tool_result_str = json.dumps(tool_result, default=str)
                self.conversation_history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result_str,
                    }
                )
                log.debug(
                    f"[LLMClient.chat] Added tool result to conversation | "
                    f"tool={fn_name} | "
                    f"result_length={len(tool_result_str)}"
                )

            # Call LLM again with tool results
            log.info(
                f"[LLMClient.chat] Calling OpenAI API with tool results | "
                f"iteration={tool_call_iteration} | "
                f"messages_count={len(self.conversation_history)}"
            )
            
            try:
                start_time = time.time()
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=self.conversation_history,
                    tools=AVAILABLE_TOOLS,
                    tool_choice="auto",
                    temperature=0.1,
                )
                api_time = time.time() - start_time
                
                log.info(
                    f"[LLMClient.chat] OpenAI API response received (iteration {tool_call_iteration}) | "
                    f"api_time={api_time:.3f}s | "
                    f"finish_reason={response.choices[0].finish_reason} | "
                    f"usage={response.usage.model_dump() if response.usage else 'N/A'}"
                )
                LLM_CALL_DURATION.labels(model=self.model, status="success").observe(api_time)
                LLM_CALL_TOTAL.labels(model=self.model, status="success").inc()
                if response.usage:
                    LLM_TOKENS.labels(model=self.model, type="prompt_tokens").inc(response.usage.prompt_tokens or 0)
                    LLM_TOKENS.labels(model=self.model, type="completion_tokens").inc(response.usage.completion_tokens or 0)
                    LLM_TOKENS.labels(model=self.model, type="total_tokens").inc(response.usage.total_tokens or 0)
            except Exception as e:
                api_time = time.time() - start_time
                error_type = type(e).__name__
                error_msg = str(e)
                LLM_CALL_DURATION.labels(model=self.model, status="error").observe(api_time)
                LLM_CALL_TOTAL.labels(model=self.model, status="error").inc()
                
                # Identificar tipo específico de erro
                if "ConnectTimeout" in error_type or "ConnectError" in error_type:
                    log.error(
                        f"[LLMClient.chat] Connection timeout/error (iteration {tool_call_iteration}) | "
                        f"error_type={error_type} | "
                        f"elapsed={api_time:.3f}s | "
                        f"error={error_msg[:200]}"
                    )
                elif "APITimeoutError" in error_type:
                    log.error(
                        f"[LLMClient.chat] API timeout (iteration {tool_call_iteration}) | "
                        f"error_type={error_type} | "
                        f"elapsed={api_time:.3f}s | "
                        f"error={error_msg[:200]}"
                    )
                else:
                    log.error(
                        f"[LLMClient.chat] OpenAI API call failed (iteration {tool_call_iteration}) | "
                        f"error_type={error_type} | "
                        f"elapsed={api_time:.3f}s | "
                        f"error={error_msg[:200]}"
                    )
                raise
                
            message = response.choices[0].message

        # Final text response
        assistant_message = message.content or ""
        self.conversation_history.append(
            {"role": "assistant", "content": assistant_message}
        )
        
        log.info(
            f"[LLMClient.chat] Chat completed | "
            f"response_length={len(assistant_message)} | "
            f"total_tool_iterations={tool_call_iteration} | "
            f"final_conversation_length={len(self.conversation_history)}"
        )

        return assistant_message

    def reset(self):
        """Reset conversation history."""
        self.conversation_history = []
