"""Container de Injeção de Dependência simples.

Constrói os adapters concretos a partir do `infrastructure.config.config`
e os entrega aos use cases. Os adapters criam um `MCPClient` por chamada
para preservar o comportamento da versão atual (cliente curto, fechado
após cada operação).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Awaitable, Callable

from application.use_cases.chat import ChatSessionRegistry, ChatUseCase
from application.use_cases.investigate import InvestigateUseCase
from domain.correlation import CorrelationEngine
from domain.guardrails import Guardrails
from domain.hypothesis import HypothesisGenerator
from infrastructure.adapters import (
    GrafanaAlertAdapter,
    InMemoryCaseFileRepository,
    OpenAILLMAdapter,
    PgIncidentAdapter,
    TempoTraceAdapter,
    VMMetricAdapter,
)
from infrastructure.config import config
from infrastructure.mcp_client import MCPClient
from infrastructure.prometheus_metrics import (
    MCP_CALL_DURATION,
    MCP_CALL_TOTAL,
    PII_REDACTIONS,
)

log = logging.getLogger("orchestrator")


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

_correlation_engine = CorrelationEngine(config.standard_labels, config.label_aliases)
_hypothesis_generator = HypothesisGenerator(metrics_catalog=config.metrics_catalog)
_guardrails = Guardrails()
_case_file_repository = InMemoryCaseFileRepository()
_chat_registry = ChatSessionRegistry()


# ---------------------------------------------------------------------------
# MCP client factories (curtos, criados sob demanda)
# ---------------------------------------------------------------------------

def _grafana_client() -> MCPClient:
    cfg = config.mcp_servers["grafana"]
    return MCPClient("grafana", cfg.endpoint, cfg.timeout)


def _incidents_client() -> MCPClient:
    cfg = config.mcp_servers["incidents-pg"]
    return MCPClient("incidents-pg", cfg.endpoint, cfg.timeout)


def _vm_client() -> MCPClient:
    cfg = config.mcp_servers["victoriametrics"]
    return MCPClient("victoriametrics", cfg.endpoint, cfg.timeout)


def _tempo_client() -> MCPClient:
    cfg = config.mcp_servers["tempo"]
    return MCPClient("tempo", cfg.endpoint, cfg.timeout)


# ---------------------------------------------------------------------------
# Adapters efêmeros que abrem/fecham conexão por investigação
# ---------------------------------------------------------------------------

class _ManagedAlertSource:
    """Wraps GrafanaAlertAdapter + MCPClient lifecycle."""

    def __init__(self):
        self._client: MCPClient | None = None
        self._adapter: GrafanaAlertAdapter | None = None

    async def __aenter__(self):
        self._client = _grafana_client()
        self._adapter = GrafanaAlertAdapter(self._client)
        return self._adapter

    async def __aexit__(self, exc_type, exc, tb):
        if self._client:
            await self._client.close()


class _ManagedIncidentSource:
    def __init__(self):
        self._client: MCPClient | None = None
        self._adapter: PgIncidentAdapter | None = None

    async def __aenter__(self):
        self._client = _incidents_client()
        self._adapter = PgIncidentAdapter(self._client)
        return self._adapter

    async def __aexit__(self, exc_type, exc, tb):
        if self._client:
            await self._client.close()


class _ManagedMetricSource:
    def __init__(self):
        self._client: MCPClient | None = None
        self._adapter: VMMetricAdapter | None = None

    async def __aenter__(self):
        self._client = _vm_client()
        self._adapter = VMMetricAdapter(self._client)
        return self._adapter

    async def __aexit__(self, exc_type, exc, tb):
        if self._client:
            await self._client.close()


class _ManagedTraceSource:
    def __init__(self):
        self._client: MCPClient | None = None
        self._adapter: TempoTraceAdapter | None = None

    async def __aenter__(self):
        self._client = _tempo_client()
        self._adapter = TempoTraceAdapter(self._client)
        return self._adapter

    async def __aexit__(self, exc_type, exc, tb):
        if self._client:
            await self._client.close()


# ---------------------------------------------------------------------------
# Use case factories
# ---------------------------------------------------------------------------

async def build_investigate_use_case_context():
    """Context manager assíncrono para criar um InvestigateUseCase com adapters
    abertos e garantir que sejam fechados ao final.

    Uso:
        async with build_investigate_use_case_context() as use_case:
            case_file = await use_case.execute(input_data, filters)
    """

    return _InvestigateContext()


class _InvestigateContext:
    """Async context manager que abre adapters e cria o use case."""

    async def __aenter__(self) -> InvestigateUseCase:
        self._alerts_cm = _ManagedAlertSource()
        self._incidents_cm = _ManagedIncidentSource()
        self._metrics_cm = _ManagedMetricSource()
        self._traces_cm = _ManagedTraceSource()

        self.alerts = await self._alerts_cm.__aenter__()
        self.incidents = await self._incidents_cm.__aenter__()
        self.metrics = await self._metrics_cm.__aenter__()
        self.traces = await self._traces_cm.__aenter__()

        return InvestigateUseCase(
            alert_source=self.alerts,
            incident_source=self.incidents,
            metric_source=self.metrics,
            trace_source=self.traces,
            correlation_engine=_correlation_engine,
            hypothesis_generator=_hypothesis_generator,
            guardrails=_guardrails,
            metrics_catalog=config.metrics_catalog,
            traces_catalog=getattr(config, "traces_catalog", []) or [],
            case_file_repository=_case_file_repository,
        )

    async def __aexit__(self, exc_type, exc, tb):
        await self._traces_cm.__aexit__(exc_type, exc, tb)
        await self._metrics_cm.__aexit__(exc_type, exc, tb)
        await self._incidents_cm.__aexit__(exc_type, exc, tb)
        await self._alerts_cm.__aexit__(exc_type, exc, tb)


def get_chat_registry() -> ChatSessionRegistry:
    return _chat_registry


def build_chat_use_case() -> ChatUseCase:
    """Factory de ChatUseCase. Cada session_id mapeia para uma instância."""
    return ChatUseCase(llm=OpenAILLMAdapter())


def get_case_file_repository() -> InMemoryCaseFileRepository:
    return _case_file_repository


# ---------------------------------------------------------------------------
# Tool executor para o LLM (function calling)
# ---------------------------------------------------------------------------

# Ferramentas roteadas para cada MCP server (mantém o set da versão atual).
GRAFANA_TOOLS = {"find_firing_alerts", "get_alert_details", "find_dashboards", "get_panel_link"}
INCIDENTS_TOOLS = {
    "get_incident",
    "search_incidents",
    "get_related_incidents",
    "get_incident_stats",
}
VM_TOOLS = {
    "query",
    "query_range",
    "metrics",
    "labels",
    "label_values",
    "series",
    "rules",
    "alerts",
    "tsdb_status",
    "top_queries",
    "active_queries",
    "metric_statistics",
    "documentation",
    "prettify_query",
    "explain_query",
    "metrics_metadata",
    "tenants",
}
TEMPO_TOOLS = {
    "traceql-search",
    "traceql-metrics-instant",
    "traceql-metrics-range",
    "get-trace",
    "get-attribute-names",
    "get-attribute-values",
    "docs-traceql",
}


async def execute_tool(tool_name: str, arguments: dict) -> dict:
    """Roteador de tools chamadas pelo LLM.

    Aplica métricas Prometheus e PII redaction de forma consistente.
    Aposentado de `_execute_tool` em orchestrator/orchestrator.py.
    """
    start_time = time.time()
    log.info(f"[execute_tool] start | tool={tool_name} | args={arguments}")

    if tool_name in GRAFANA_TOOLS:
        client = _grafana_client()
        server_name = "grafana"
    elif tool_name in INCIDENTS_TOOLS:
        client = _incidents_client()
        server_name = "incidents-pg"
    elif tool_name in VM_TOOLS:
        client = _vm_client()
        server_name = "victoriametrics"
    elif tool_name in TEMPO_TOOLS:
        client = _tempo_client()
        server_name = "tempo"
    else:
        log.error(f"[execute_tool] Unknown tool: {tool_name}")
        return {"error": f"Unknown tool: {tool_name}"}

    try:
        result = await client.call_tool(tool_name, arguments)

        # PII redaction
        result_str, redacted = Guardrails.redact_pii(json.dumps(result, default=str))
        result = json.loads(result_str)
        if redacted:
            PII_REDACTIONS.inc()

        execution_time = time.time() - start_time
        MCP_CALL_DURATION.labels(server=server_name, tool=tool_name, status="success").observe(execution_time)
        MCP_CALL_TOTAL.labels(server=server_name, tool=tool_name, status="success").inc()

        log.info(
            f"[execute_tool] OK | tool={tool_name} | server={server_name} | "
            f"execution_time={execution_time:.3f}s | pii_redacted={redacted}"
        )
        return result

    except Exception as e:  # noqa: BLE001
        execution_time = time.time() - start_time
        MCP_CALL_DURATION.labels(server=server_name, tool=tool_name, status="error").observe(execution_time)
        MCP_CALL_TOTAL.labels(server=server_name, tool=tool_name, status="error").inc()
        log.error(
            f"[execute_tool] FAILED | tool={tool_name} | server={server_name} | "
            f"execution_time={execution_time:.3f}s | error_type={type(e).__name__} | error={str(e)[:200]}"
        )
        return {"error": f"Tool execution failed: {str(e)}"}

    finally:
        await client.close()


ToolExecutor = Callable[[str, dict], Awaitable[Any]]
