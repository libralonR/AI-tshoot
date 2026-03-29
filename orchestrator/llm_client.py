"""LLM client for conversational investigation using OpenAI."""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI
from pathlib import Path

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
            "description": "Find incidents related to a specific incident (same CI or parent_incident) or service.",
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
]


class LLMClient:
    """OpenAI-based LLM client with function calling for tool use."""

    def __init__(self, mcp_tools: Dict[str, Any] = None):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY env var not set")

        self.client = AsyncOpenAI(api_key=api_key)
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o")
        self.system_prompt = _load_system_prompt()
        self.mcp_tools = mcp_tools or {}
        self.conversation_history: List[Dict[str, Any]] = []

    async def chat(
        self,
        user_message: str,
        tool_executor: Any = None,
    ) -> str:
        """Send a message and get a response, with automatic tool calling."""

        # Add system prompt on first message
        if not self.conversation_history:
            self.conversation_history.append(
                {"role": "system", "content": self.system_prompt}
            )

        # Add user message
        self.conversation_history.append({"role": "user", "content": user_message})

        # Call LLM with tools
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=self.conversation_history,
            tools=AVAILABLE_TOOLS,
            tool_choice="auto",
            temperature=0.1,
        )

        message = response.choices[0].message

        # Handle tool calls (function calling)
        while message.tool_calls:
            self.conversation_history.append(message.model_dump())

            for tool_call in message.tool_calls:
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments)

                log.info(f"LLM calling tool: {fn_name}({fn_args})")

                # Execute tool via MCP
                if tool_executor:
                    tool_result = await tool_executor(fn_name, fn_args)
                else:
                    tool_result = {"error": "No tool executor configured"}

                # Add tool result to conversation
                self.conversation_history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(tool_result, default=str),
                    }
                )

            # Call LLM again with tool results
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=self.conversation_history,
                tools=AVAILABLE_TOOLS,
                tool_choice="auto",
                temperature=0.1,
            )
            message = response.choices[0].message

        # Final text response
        assistant_message = message.content or ""
        self.conversation_history.append(
            {"role": "assistant", "content": assistant_message}
        )

        return assistant_message

    def reset(self):
        """Reset conversation history."""
        self.conversation_history = []
