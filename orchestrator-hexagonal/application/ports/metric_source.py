"""Port para fontes de métricas (VictoriaMetrics, Prometheus, Mimir, etc.)."""

from typing import Any, Dict, List, Optional, Protocol

from domain.models import Evidence


class MetricSource(Protocol):
    """Contrato para qualquer adapter que provê métricas (PromQL/MetricsQL)."""

    async def execute_query(
        self,
        query: str,
        description: str = "",
        time: Optional[str] = None,
        step: Optional[str] = None,
    ) -> Optional[Evidence]:
        """Instant query."""
        ...

    async def execute_range_query(
        self,
        query: str,
        start: str,
        end: Optional[str] = None,
        step: str = "1m",
        description: str = "",
    ) -> Optional[Evidence]:
        """Range query."""
        ...

    async def execute_alert_expression(
        self,
        alert_data: Dict[str, Any],
    ) -> List[Evidence]:
        """Executar a expressão PromQL embutida em um alerta Grafana."""
        ...

    async def execute_catalog_queries(
        self,
        service_name: str,
        catalog: List[Dict[str, str]],
    ) -> List[Evidence]:
        """Executar todas as queries do metrics-catalog para um serviço."""
        ...
