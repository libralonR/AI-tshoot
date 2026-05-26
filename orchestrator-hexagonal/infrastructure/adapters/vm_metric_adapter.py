"""Adapter VictoriaMetrics → MetricSource.

Substitui o `agents/metrics.py` da versão atual.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from domain.guardrails import Guardrails
from domain.models import Evidence, EvidenceType
from infrastructure.mcp_client import MCPClient

log = logging.getLogger("orchestrator")


class VMMetricAdapter:
    """MetricSource implementation backed by VictoriaMetrics MCP (proxy ou nativo)."""

    def __init__(self, mcp_client: MCPClient):
        self.mcp = mcp_client

    async def execute_query(
        self,
        query: str,
        description: str = "",
        time: Optional[str] = None,
        step: Optional[str] = None,
    ) -> Optional[Evidence]:
        args: Dict[str, Any] = {"query": query}
        if time:
            args["time"] = time
        if step:
            args["step"] = step

        log.info(f"[VMMetricAdapter.execute_query] query={query[:100]}")
        result = await self.mcp.call_tool("query", args)

        if not result.get("success", True):
            log.error(f"[VMMetricAdapter.execute_query] Failed: {result.get('error')}")
            return None

        result_str = json.dumps(result, default=str)
        redacted_str, was_redacted = Guardrails.redact_pii(result_str)
        redacted_result = json.loads(redacted_str)

        return Evidence(
            id=str(uuid.uuid4()),
            type=EvidenceType.METRIC_ANOMALY,
            source="victoriametrics-mcp",
            query=query,
            result=redacted_result,
            timestamp=datetime.utcnow().isoformat(),
            links=[],
            confidence=0.8,
            redacted=was_redacted,
        )

    async def execute_range_query(
        self,
        query: str,
        start: str,
        end: Optional[str] = None,
        step: str = "1m",
        description: str = "",
    ) -> Optional[Evidence]:
        args: Dict[str, Any] = {"query": query, "start": start, "step": step}
        if end:
            args["end"] = end

        log.info(
            f"[VMMetricAdapter.execute_range_query] query={query[:100]} | "
            f"start={start} | step={step}"
        )
        result = await self.mcp.call_tool("query_range", args)

        if not result.get("success", True):
            log.error(f"[VMMetricAdapter.execute_range_query] Failed: {result.get('error')}")
            return None

        result_str = json.dumps(result, default=str)
        redacted_str, was_redacted = Guardrails.redact_pii(result_str)
        redacted_result = json.loads(redacted_str)

        return Evidence(
            id=str(uuid.uuid4()),
            type=EvidenceType.METRIC_ANOMALY,
            source="victoriametrics-mcp",
            query=f"query_range({query}, start={start}, step={step})",
            result=redacted_result,
            timestamp=datetime.utcnow().isoformat(),
            links=[],
            confidence=0.8,
            redacted=was_redacted,
        )

    async def execute_alert_expression(
        self,
        alert_data: Dict[str, Any],
    ) -> List[Evidence]:
        evidences: List[Evidence] = []
        for dq in alert_data.get("data", []):
            model = dq.get("model", {})
            expr = model.get("expr") or dq.get("expr")
            ref_id = dq.get("refId", "?")
            if not expr:
                continue
            ds_type = model.get("datasource", {}).get("type", "")
            if ds_type in ("__expr__", "-100"):
                continue

            log.info(
                f"[VMMetricAdapter.execute_alert_expression] "
                f"refId={ref_id} | expr={expr[:100]}"
            )
            evidence = await self.execute_query(
                query=expr,
                description=f"Alert expression (refId={ref_id})",
            )
            if evidence:
                evidence.result["_alert_ref_id"] = ref_id
                evidence.result["_alert_expression"] = expr
                evidences.append(evidence)
        return evidences

    async def execute_catalog_queries(
        self,
        service_name: str,
        catalog: List[Dict[str, str]],
    ) -> List[Evidence]:
        evidences: List[Evidence] = []
        for entry in catalog:
            query = entry["query_template"].replace("{service}", service_name)
            name = entry.get("name", "catalog_query")
            category = entry.get("category", "unknown")

            log.info(
                f"[VMMetricAdapter.execute_catalog_queries] "
                f"name={name} | category={category} | query={query[:100]}"
            )
            evidence = await self.execute_query(query=query, description=name)
            if evidence:
                evidence.result["_catalog_name"] = name
                evidence.result["_catalog_category"] = category
                evidences.append(evidence)
        return evidences
