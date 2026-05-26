"""Port para fontes de incidentes (PostgreSQL espelho do ServiceNow, etc.)."""

from typing import List, Optional, Protocol

from domain.models import Evidence


class IncidentSource(Protocol):
    """Contrato para qualquer adapter que provê incidentes."""

    async def fetch_incident(self, number: str) -> Optional[Evidence]:
        """Buscar um incidente específico pelo número (ex: INC0012345)."""
        ...

    async def find_related_incidents(
        self,
        number: Optional[str] = None,
        application_service: Optional[str] = None,
        time_window_hours: int = 24,
    ) -> List[Evidence]:
        """Buscar incidentes relacionados (parent/CI/labels do Grafana)."""
        ...
