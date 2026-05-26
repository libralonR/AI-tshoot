"""Port para fontes de traces (Tempo, Jaeger, etc.)."""

from typing import Any, Dict, Protocol


class TraceSource(Protocol):
    """Contrato para qualquer adapter que provê traces (TraceQL)."""

    async def query_traces(
        self,
        query: str,
        limit: int = 20,
        start: str = "",
        end: str = "",
    ) -> Dict[str, Any]:
        """Buscar traces via TraceQL."""
        ...

    async def get_trace(self, trace_id: str) -> Dict[str, Any]:
        """Recuperar um trace específico pelo ID."""
        ...
