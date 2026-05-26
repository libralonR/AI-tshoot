"""Port para fontes de alertas (Grafana, Mimir, etc.)."""

from typing import List, Optional, Protocol

from domain.models import Evidence, Scope


class AlertSource(Protocol):
    """Contrato para qualquer adapter que provê alertas.

    Implementações típicas:
      - GrafanaAlertAdapter (HTTP via MCP)
      - InMemoryAlertAdapter (testes)
    """

    async def fetch_alert_details(self, alert_uid: str) -> Optional[Evidence]:
        """Buscar detalhes de um alerta específico por UID."""
        ...

    async def find_firing_alerts(self, scope: Scope) -> List[Evidence]:
        """Listar alertas em firing aplicando o escopo (labels)."""
        ...
