"""Port para fontes de traces (Tempo, Jaeger, etc.)."""

from typing import Any, Dict, List, Optional, Protocol

from domain.models import Evidence


class TraceSource(Protocol):
    """Contrato para qualquer adapter que provê traces (TraceQL).

    Implementações típicas:
      - TempoTraceAdapter (HTTP via MCP JSON-RPC)
      - InMemoryTraceAdapter (testes)
    """

    async def query_traces(
        self,
        query: str,
        limit: int = 20,
        start: str = "",
        end: str = "",
    ) -> Dict[str, Any]:
        """Buscar traces via TraceQL (raw)."""
        ...

    async def get_trace(self, trace_id: str) -> Dict[str, Any]:
        """Recuperar um trace específico pelo ID (raw)."""
        ...

    async def search_traces(
        self,
        query: str,
        limit: int = 10,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Optional[Evidence]:
        """traceql-search retornando uma Evidence agregada."""
        ...

    async def metrics_instant(
        self,
        query: str,
        time: Optional[str] = None,
    ) -> Optional[Evidence]:
        """traceql-metrics-instant retornando Evidence."""
        ...

    async def metrics_range(
        self,
        query: str,
        start: str,
        end: Optional[str] = None,
        step: str = "5m",
    ) -> Optional[Evidence]:
        """traceql-metrics-range retornando Evidence."""
        ...

    async def fetch_trace_id_from_alert(self, trace_id: str) -> Optional[Evidence]:
        """Atalho: puxar um trace por ID quando o alerta carrega trace_id."""
        ...

    async def execute_catalog_queries(
        self,
        service_name: str,
        catalog: List[Dict[str, Any]],
        time_window_start: Optional[str] = None,
        time_window_end: Optional[str] = None,
    ) -> List[Evidence]:
        """Executar todas as queries do traces-catalog para um serviço."""
        ...
