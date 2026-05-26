"""Adapter OpenAI → LLMProvider.

Mesma lógica do `orchestrator/llm_client.py` da versão atual, mas:
- Implementa o port `LLMProvider`
- Carrega o system prompt e o catálogo via `infrastructure.config.config`
- Mantém todas as métricas Prometheus `observa_*`
- Mantém os 7 timeouts/retries específicos para o LLM Gateway corporativo
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List

import httpx
from openai import AsyncOpenAI

from infrastructure.adapters.tools_catalog import AVAILABLE_TOOLS
from infrastructure.config import PROMPTS_DIR, config
from infrastructure.prometheus_metrics import (
    LLM_CALL_DURATION,
    LLM_CALL_TOTAL,
    LLM_TOKENS,
    LLM_TOOL_CALLS,
)

log = logging.getLogger("orchestrator")


def _load_system_prompt() -> str:
    """Carrega o orchestrator-prompt.md e anexa o catálogo de queries."""
    prompt_file = Path(PROMPTS_DIR) / "orchestrator-prompt.md"
    if prompt_file.exists():
        base_prompt = prompt_file.read_text()
    else:
        base_prompt = "You are an observability troubleshooting copilot for SRE teams."

    if config.metrics_catalog:
        catalog_section = "\n\n## Catálogo de Queries PromQL (pré-definidas)\n\n"
        catalog_section += "Use estas queries quando precisar consultar métricas. "
        catalog_section += "Substitua `{service}` pelo `application_service` real.\n\n"
        for entry in config.metrics_catalog:
            name = entry.get("name", "")
            category = entry.get("category", "")
            query = entry.get("query_template", "")
            desc = entry.get("description", "")
            catalog_section += f"- **{name}** ({category}): `{query}`\n"
            if desc:
                catalog_section += f"  {desc}\n"
        base_prompt += catalog_section

    return base_prompt


class OpenAILLMAdapter:
    """LLMProvider implementation backed by OpenAI Async API."""

    def __init__(self, mcp_tools: Dict[str, Any] = None):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            log.error("[OpenAILLMAdapter.__init__] OPENAI_API_KEY env var not set")
            raise RuntimeError("OPENAI_API_KEY env var not set")

        timeout = float(os.getenv("OPENAI_TIMEOUT", "60"))
        connect_timeout = float(os.getenv("OPENAI_CONNECT_TIMEOUT", "10"))

        http_client = httpx.AsyncClient(
            verify=False,
            timeout=httpx.Timeout(
                timeout=timeout,
                connect=connect_timeout,
                read=timeout,
                write=10.0,
                pool=5.0,
            ),
        )

        base_url = os.getenv("OPENAI_BASE_URL")
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=http_client,
            max_retries=2,
        )
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o")
        self.system_prompt = _load_system_prompt()
        self.mcp_tools = mcp_tools or {}
        self.conversation_history: List[Dict[str, Any]] = []

        log.info(
            f"[OpenAILLMAdapter.__init__] Initialized | model={self.model} | "
            f"base_url={base_url or 'default'} | timeout={timeout}s | "
            f"connect_timeout={connect_timeout}s | max_retries=2 | "
            f"system_prompt_length={len(self.system_prompt)} | "
            f"available_tools={len(AVAILABLE_TOOLS)}"
        )

    # ------------------------------------------------------------------
    # LLMProvider port
    # ------------------------------------------------------------------

    async def chat(self, user_message: str, tool_executor) -> str:
        log.info(
            f"[OpenAILLMAdapter.chat] Starting | user_message_length={len(user_message)} | "
            f"history_length={len(self.conversation_history)}"
        )

        if not self.conversation_history:
            self.conversation_history.append(
                {"role": "system", "content": self.system_prompt}
            )

        self.conversation_history.append({"role": "user", "content": user_message})

        response = await self._call_openai("initial")
        message = response.choices[0].message
        tool_call_iteration = 0

        while message.tool_calls:
            tool_call_iteration += 1
            log.info(
                f"[OpenAILLMAdapter.chat] Processing tool calls | "
                f"iteration={tool_call_iteration} | calls={len(message.tool_calls)}"
            )
            self.conversation_history.append(message.model_dump())

            for idx, tool_call in enumerate(message.tool_calls, 1):
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments)

                log.info(
                    f"[OpenAILLMAdapter.chat] Executing tool {idx}/{len(message.tool_calls)} | "
                    f"tool={fn_name} | args={fn_args}"
                )

                tool_start = time.time()
                if tool_executor:
                    try:
                        tool_result = await tool_executor(fn_name, fn_args)
                        tool_time = time.time() - tool_start
                        LLM_TOOL_CALLS.labels(tool=fn_name, status="success").inc()
                        log.info(
                            f"[OpenAILLMAdapter.chat] Tool OK | tool={fn_name} | "
                            f"execution_time={tool_time:.3f}s"
                        )
                    except Exception as e:  # noqa: BLE001
                        tool_time = time.time() - tool_start
                        LLM_TOOL_CALLS.labels(tool=fn_name, status="error").inc()
                        log.error(
                            f"[OpenAILLMAdapter.chat] Tool FAILED | tool={fn_name} | "
                            f"error_type={type(e).__name__} | error={str(e)[:200]}"
                        )
                        tool_result = {"error": f"Tool execution failed: {str(e)}"}
                else:
                    tool_result = {"error": "No tool executor configured"}

                tool_result_str = json.dumps(tool_result, default=str)
                if len(tool_result_str) > 50000:
                    tool_result_str = self._truncate_tool_result(tool_result, fn_name)

                self.conversation_history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result_str,
                    }
                )

            response = await self._call_openai(f"iteration {tool_call_iteration}")
            message = response.choices[0].message

        assistant_message = message.content or ""
        self.conversation_history.append(
            {"role": "assistant", "content": assistant_message}
        )

        log.info(
            f"[OpenAILLMAdapter.chat] Completed | "
            f"response_length={len(assistant_message)} | "
            f"total_tool_iterations={tool_call_iteration}"
        )
        return assistant_message

    def reset(self) -> None:
        self.conversation_history = []

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _call_openai(self, label: str):
        start_time = time.time()
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=self.conversation_history,
                tools=AVAILABLE_TOOLS,
                tool_choice="auto",
            )
            api_time = time.time() - start_time

            log.info(
                f"[OpenAILLMAdapter._call_openai] {label} OK | api_time={api_time:.3f}s | "
                f"finish_reason={response.choices[0].finish_reason} | "
                f"usage={response.usage.model_dump() if response.usage else 'N/A'}"
            )
            LLM_CALL_DURATION.labels(model=self.model, status="success").observe(api_time)
            LLM_CALL_TOTAL.labels(model=self.model, status="success").inc()
            if response.usage:
                LLM_TOKENS.labels(model=self.model, type="prompt_tokens").inc(
                    response.usage.prompt_tokens or 0
                )
                LLM_TOKENS.labels(model=self.model, type="completion_tokens").inc(
                    response.usage.completion_tokens or 0
                )
                LLM_TOKENS.labels(model=self.model, type="total_tokens").inc(
                    response.usage.total_tokens or 0
                )
            return response

        except Exception as e:  # noqa: BLE001
            api_time = time.time() - start_time
            error_type = type(e).__name__
            error_msg = str(e)
            LLM_CALL_DURATION.labels(model=self.model, status="error").observe(api_time)
            LLM_CALL_TOTAL.labels(model=self.model, status="error").inc()

            if "ConnectTimeout" in error_type or "ConnectError" in error_type:
                log.error(
                    f"[OpenAILLMAdapter._call_openai] {label} connection timeout/error | "
                    f"error_type={error_type} | elapsed={api_time:.3f}s | "
                    f"base_url={os.getenv('OPENAI_BASE_URL')} | error={error_msg[:200]}"
                )
                raise RuntimeError(
                    f"Não foi possível conectar ao LLM Gateway. "
                    f"URL: {os.getenv('OPENAI_BASE_URL')} | Erro: {error_msg[:100]}"
                ) from e
            if "APITimeoutError" in error_type:
                log.error(
                    f"[OpenAILLMAdapter._call_openai] {label} API timeout | "
                    f"error_type={error_type} | elapsed={api_time:.3f}s | "
                    f"timeout={os.getenv('OPENAI_TIMEOUT', '60')}s | error={error_msg[:200]}"
                )
                raise RuntimeError(
                    f"Timeout ao chamar LLM Gateway após {api_time:.1f}s. "
                    f"Aumente OPENAI_TIMEOUT (atual: {os.getenv('OPENAI_TIMEOUT', '60')}s)."
                ) from e
            if "InternalServerError" in error_type or "504" in error_msg:
                log.error(
                    f"[OpenAILLMAdapter._call_openai] {label} 504 gateway timeout | "
                    f"error_type={error_type} | elapsed={api_time:.3f}s | "
                    f"error={error_msg[:200]}"
                )
                raise RuntimeError(
                    f"O LLM Gateway retornou timeout (504) após {api_time:.1f}s. "
                    f"Tente uma pergunta mais específica."
                ) from e

            log.error(
                f"[OpenAILLMAdapter._call_openai] {label} failed | error_type={error_type} | "
                f"elapsed={api_time:.3f}s | error={error_msg[:200]}"
            )
            raise

    @staticmethod
    def _truncate_tool_result(result: dict, tool_name: str, max_items: int = 15) -> str:
        truncated = dict(result)

        if "result" in truncated and isinstance(truncated["result"], dict):
            inner = truncated["result"]
            for key in ("by_description", "by_ci", "by_parent"):
                items = inner.get(key, [])
                if len(items) > max_items:
                    inner[key] = items[:max_items]
                    inner[f"_{key}_truncated"] = True
                    inner[f"_{key}_total"] = len(items)

        if "result" in truncated and isinstance(truncated["result"], list):
            items = truncated["result"]
            if len(items) > max_items:
                truncated["result"] = items[:max_items]
                truncated["_truncated"] = True
                truncated["_total_results"] = len(items)
                truncated["_shown_results"] = max_items

        return json.dumps(truncated, default=str)
