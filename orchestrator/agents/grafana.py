"""Grafana specialist agent."""

import json
import logging
import uuid
from datetime import datetime
from typing import List, Optional

from guardrails import Guardrails
from mcp_client import MCPClient
from models import Evidence, EvidenceType, Scope

log = logging.getLogger("orchestrator")


class GrafanaAgent:
    """Specialist agent for Grafana queries."""

    def __init__(self, mcp_client: MCPClient):
        self.mcp = mcp_client

    async def fetch_alert_details(self, alert_uid: str) -> Optional[Evidence]:
        result = await self.mcp.call_tool("get_alert_details", {"alertUID": alert_uid})
        if not result.get("success"):
            log.error(f"Failed to fetch alert details: {result.get('error')}")
            return None

        result_str = json.dumps(result["result"])
        redacted_str, was_redacted = Guardrails.redact_pii(result_str)
        redacted_result = json.loads(redacted_str)

        return Evidence(
            id=str(uuid.uuid4()),
            type=EvidenceType.ALERT_FIRING,
            source="grafana-mcp",
            query=f"get_alert_details(alertUID={alert_uid})",
            result=redacted_result,
            timestamp=datetime.utcnow().isoformat(),
            links=[result.get("alertURL", "")],
            confidence=0.9,
            redacted=was_redacted,
        )

    async def find_firing_alerts(self, scope: Scope) -> List[Evidence]:
        labels = {}
        if scope.serviceName:
            labels["application_service"] = scope.serviceName

        # Pass all additional labels as filters
        additional = scope.additionalLabels or {}
        for key in ("owner_squad", "business_capability", "severidade", "grafana_folder", "alertname"):
            val = additional.get(key)
            if val:
                # Map severidade back to Grafana label name
                label_key = "Severidade" if key == "severidade" else key
                labels[label_key] = val

        result = await self.mcp.call_tool("find_firing_alerts", {"labels": labels})
        if not result.get("success"):
            log.error(f"Failed to find firing alerts: {result.get('error')}")
            return []

        evidences = []
        for alert in result.get("result", []):
            alert_str = json.dumps(alert)
            redacted_str, was_redacted = Guardrails.redact_pii(alert_str)
            redacted_alert = json.loads(redacted_str)
            evidences.append(
                Evidence(
                    id=str(uuid.uuid4()),
                    type=EvidenceType.ALERT_FIRING,
                    source="grafana-mcp",
                    query=f"find_firing_alerts(labels={labels})",
                    result=redacted_alert,
                    timestamp=datetime.utcnow().isoformat(),
                    links=[alert.get("generatorURL", "")],
                    confidence=0.85,
                    redacted=was_redacted,
                )
            )
        return evidences
