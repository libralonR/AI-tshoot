"""Incidents specialist agent (PostgreSQL)."""

import json
import logging
import uuid
from datetime import datetime
from typing import List, Optional

from guardrails import Guardrails
from mcp_client import MCPClient
from models import Evidence, EvidenceType

log = logging.getLogger("orchestrator")


class IncidentsAgent:
    """Specialist agent for incident queries (PostgreSQL)."""

    def __init__(self, mcp_client: MCPClient):
        self.mcp = mcp_client

    async def fetch_incident(self, number: str) -> Optional[Evidence]:
        result = await self.mcp.call_tool("get_incident", {"number": number})
        if not result.get("success"):
            log.error(f"Failed to fetch incident: {result.get('error')}")
            return None

        result_str = json.dumps(result["result"])
        redacted_str, was_redacted = Guardrails.redact_pii(result_str)
        redacted_result = json.loads(redacted_str)

        return Evidence(
            id=str(uuid.uuid4()),
            type=EvidenceType.INCIDENT_RELATED,
            source="incidents-pg-mcp",
            query=f"get_incident(number={number})",
            result=redacted_result,
            timestamp=datetime.utcnow().isoformat(),
            links=[],
            confidence=0.95,
            redacted=was_redacted,
        )

    async def find_related_incidents(
        self,
        number: str = None,
        cmdb_ci_name: str = None,
        time_window_hours: int = 24,
    ) -> List[Evidence]:
        args = {"time_window_hours": time_window_hours}
        if number:
            args["number"] = number
        if cmdb_ci_name:
            args["cmdb_ci_name"] = cmdb_ci_name

        result = await self.mcp.call_tool("get_related_incidents", args)
        if not result.get("success"):
            log.error(f"Failed to find related incidents: {result.get('error')}")
            return []

        evidences = []
        all_incidents = result.get("result", {})
        # Iterar sobre todas as fontes: by_parent, by_ci, by_description
        for source_key in ("by_parent", "by_ci", "by_description"):
            for inc in all_incidents.get(source_key, []):
                inc_str = json.dumps(inc)
                redacted_str, was_redacted = Guardrails.redact_pii(inc_str)
                redacted_inc = json.loads(redacted_str)
                evidences.append(
                    Evidence(
                        id=str(uuid.uuid4()),
                        type=EvidenceType.INCIDENT_RELATED,
                        source="incidents-pg-mcp",
                        query=f"get_related_incidents({args})",
                        result=redacted_inc,
                        timestamp=datetime.utcnow().isoformat(),
                        links=[],
                        confidence=0.7,
                        redacted=was_redacted,
                    )
                )
        return evidences
