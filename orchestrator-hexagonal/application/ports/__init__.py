"""Ports: contratos que o domínio define para o mundo externo.

Cada Port é um Protocol que descreve uma capacidade que o use case precisa
(buscar alertas, incidentes, métricas, traces, conversar com LLM, persistir
um CaseFile). Os adapters concretos vivem em `infrastructure/adapters/`.
"""

from application.ports.alert_source import AlertSource
from application.ports.case_file_repository import CaseFileRepository
from application.ports.incident_source import IncidentSource
from application.ports.llm_provider import LLMProvider, ToolExecutor
from application.ports.metric_source import MetricSource
from application.ports.trace_source import TraceSource

__all__ = [
    "AlertSource",
    "CaseFileRepository",
    "IncidentSource",
    "LLMProvider",
    "MetricSource",
    "ToolExecutor",
    "TraceSource",
]
