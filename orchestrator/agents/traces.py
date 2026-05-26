"""Tempo traces specialist agent."""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from guardrails import Guardrails
from mcp_client import MCPClient
from models import Evidence, EvidenceType

log = logging.getLogger("orchestrator")


class TracesAgent:
    """Specialist agent for Grafana Tempo (TraceQL) queries.

    Tempo é acessado via JSON-RPC MCP nativo (handshake initialize + tools/call).
    O `MCPClient` cuida do roteamento automaticamente quando `server_name="tempo"`.
    """

    def __init__(self, mcp_client: MCPClient):
        self.mcp = mcp_client

    # ------------------------------------------------------------------
    # Low-level wrappers em torno das tools do Tempo MCP
    # ------------------------------------------------------------------

    async def search_traces(
        self,
        query: str,
        limit: int = 10,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Optional[Evidence]:
        """Executa um traceql-search e retorna uma única Evidence agregada."""
        args: Dict[str, Any] = {"query": query, "limit": limit}
        if start:
            args["start"] = start
        if end:
            args["end"] = end

        log.info(f"[TracesAgent.search_traces] query={query[:120]} | limit={limit}")
        result = await self.mcp.call_tool("traceql-search", args)

        if not result.get("success", True):
            log.error(f"[TracesAgent.search_traces] Failed: {result.get('error')}")
            return None

        result_str = json.dumps(result, default=str)
        redacted_str, was_redacted = Guardrails.redact_pii(result_str)
        redacted_result = json.loads(redacted_str)

        # Decidir tipo: error vs slow span
        ev_type = (
            EvidenceType.TRACE_ERROR
            if "status = error" in query or "status_code >= 500" in query
            else EvidenceType.TRACE_SLOW_SPAN
        )

        return Evidence(
            id=str(uuid.uuid4()),
            type=ev_type,
            source="tempo-mcp",
            query=f"traceql-search({query})",
            result=redacted_result,
            timestamp=datetime.utcnow().isoformat(),
            links=[],
            confidence=0.8,
            redacted=was_redacted,
        )

    async def metrics_instant(
        self,
        query: str,
        time: Optional[str] = None,
    ) -> Optional[Evidence]:
        args: Dict[str, Any] = {"query": query}
        if time:
            args["time"] = time

        log.info(f"[TracesAgent.metrics_instant] query={query[:120]}")
        result = await self.mcp.call_tool("traceql-metrics-instant", args)

        if not result.get("success", True):
            log.error(f"[TracesAgent.metrics_instant] Failed: {result.get('error')}")
            return None

        result_str = json.dumps(result, default=str)
        redacted_str, was_redacted = Guardrails.redact_pii(result_str)
        redacted_result = json.loads(redacted_str)

        return Evidence(
            id=str(uuid.uuid4()),
            type=EvidenceType.TRACE_SLOW_SPAN,
            source="tempo-mcp",
            query=f"traceql-metrics-instant({query})",
            result=redacted_result,
            timestamp=datetime.utcnow().isoformat(),
            links=[],
            confidence=0.75,
            redacted=was_redacted,
        )

    async def metrics_range(
        self,
        query: str,
        start: str,
        end: Optional[str] = None,
        step: str = "5m",
    ) -> Optional[Evidence]:
        args: Dict[str, Any] = {"query": query, "start": start, "step": step}
        if end:
            args["end"] = end

        log.info(f"[TracesAgent.metrics_range] query={query[:120]} | start={start} | step={step}")
        result = await self.mcp.call_tool("traceql-metrics-range", args)

        if not result.get("success", True):
            log.error(f"[TracesAgent.metrics_range] Failed: {result.get('error')}")
            return None

        result_str = json.dumps(result, default=str)
        redacted_str, was_redacted = Guardrails.redact_pii(result_str)
        redacted_result = json.loads(redacted_str)

        return Evidence(
            id=str(uuid.uuid4()),
            type=EvidenceType.TRACE_SLOW_SPAN,
            source="tempo-mcp",
            query=f"traceql-metrics-range({query}, start={start}, step={step})",
            result=redacted_result,
            timestamp=datetime.utcnow().isoformat(),
            links=[],
            confidence=0.75,
            redacted=was_redacted,
        )

    async def get_trace(self, trace_id: str) -> Optional[Evidence]:
        log.info(f"[TracesAgent.get_trace] trace_id={trace_id}")
        result = await self.mcp.call_tool("get-trace", {"trace_id": trace_id})

        if not result.get("success", True):
            log.error(f"[TracesAgent.get_trace] Failed: {result.get('error')}")
            return None

        result_str = json.dumps(result, default=str)
        redacted_str, was_redacted = Guardrails.redact_pii(result_str)
        redacted_result = json.loads(redacted_str)

        return Evidence(
            id=str(uuid.uuid4()),
            type=EvidenceType.TRACE_SLOW_SPAN,
            source="tempo-mcp",
            query=f"get-trace(trace_id={trace_id})",
            result=redacted_result,
            timestamp=datetime.utcnow().isoformat(),
            links=[],
            confidence=0.95,
            redacted=was_redacted,
        )

    # ------------------------------------------------------------------
    # Catalog execution
    # ------------------------------------------------------------------

    async def execute_catalog_queries(
        self,
        service_name: str,
        catalog: List[Dict[str, Any]],
        time_window_start: Optional[str] = None,
        time_window_end: Optional[str] = None,
    ) -> List[Evidence]:
        """Executa todas as queries do traces-catalog para um serviço.

        Cada entry tem:
          - name, category
          - kind: search | metrics_instant | metrics_range
          - query_template (com {service} placeholder)
          - limit (search) ou step (metrics_range)
        """
        evidences: List[Evidence] = []

        for entry in catalog:
            kind = entry.get("kind")
            name = entry.get("name", "tempo_query")
            category = entry.get("category", "unknown")
            query = entry["query_template"].replace("{service}", service_name)

            log.info(
                f"[TracesAgent.execute_catalog_queries] "
                f"name={name} | kind={kind} | category={category} | query={query[:120]}"
            )

            evidence: Optional[Evidence] = None
            if kind == "search":
                limit = int(entry.get("limit", 10))
                evidence = await self.search_traces(
                    query=query,
                    limit=limit,
                    start=time_window_start,
                    end=time_window_end,
                )
            elif kind == "metrics_instant":
                evidence = await self.metrics_instant(query=query, time=time_window_end)
            elif kind == "metrics_range":
                step = entry.get("step", "5m")
                if not time_window_start:
                    log.warning(
                        f"[TracesAgent.execute_catalog_queries] "
                        f"skip {name}: metrics_range requer time_window_start"
                    )
                    continue
                evidence = await self.metrics_range(
                    query=query,
                    start=time_window_start,
                    end=time_window_end,
                    step=step,
                )
            else:
                log.warning(f"[TracesAgent.execute_catalog_queries] unknown kind={kind} for {name}")
                continue

            if evidence:
                evidence.result["_catalog_name"] = name
                evidence.result["_catalog_category"] = category
                evidence.result["_catalog_kind"] = kind
                evidences.append(evidence)

        return evidences

    async def fetch_trace_id_from_alert(self, trace_id: str) -> Optional[Evidence]:
        """Atalho para puxar trace direto quando o alerta carrega um trace_id."""
        if not trace_id:
            return None
        return await self.get_trace(trace_id)
