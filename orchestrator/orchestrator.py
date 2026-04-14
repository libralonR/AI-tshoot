#!/usr/bin/env python3
"""
Observability Troubleshooting Copilot — Orchestrator
Coordinates specialist agents, applies guardrails, generates CaseFile.
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agents import GrafanaAgent, IncidentsAgent, MetricsAgent
from config import config
from correlation import CorrelationEngine
from guardrails import Guardrails
from hypothesis import HypothesisGenerator
from mcp_client import MCPClient
from metrics import (
    INVESTIGATION_DURATION,
    INVESTIGATION_TOTAL,
    EVIDENCE_COUNT,
    HYPOTHESIS_COUNT,
    CORRELATION_GAPS,
    MCP_CALL_DURATION,
    MCP_CALL_TOTAL,
    CHAT_DURATION,
    CHAT_TOTAL,
    CHAT_SESSIONS_ACTIVE,
    LLM_TOOL_CALLS,
    PII_REDACTIONS,
)
from models import (
    AuditEntry,
    CaseFile,
    Evidence,
    Input,
    InputType,
    InvestigateRequest,
    InvestigateResponse,
    Scope,
    TimeWindow,
)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("orchestrator")

# FastAPI
app = FastAPI(
    title="Observability Troubleshooting Copilot",
    description="AI-powered incident triage and root cause analysis",
    version="1.0.0",
)


class Orchestrator:
    """Main orchestrator that coordinates investigation."""

    def __init__(self):
        self.config = config
        self.correlation_engine = CorrelationEngine(
            config.standard_labels, config.label_aliases
        )
        self.hypothesis_generator = HypothesisGenerator(
            metrics_catalog=config.metrics_catalog
        )
        self.guardrails = Guardrails()

    async def investigate(self, input_data: Input, filters: dict = None) -> CaseFile:
        start_time = time.time()
        status = "success"
        
        log.info(
            f"[investigate] Starting investigation | "
            f"type={input_data.type} | "
            f"value={input_data.value[:50]}... | "
            f"user={input_data.user} | "
            f"filters={filters}"
        )

        try:
            if not self._validate_input(input_data):
                log.error(f"[investigate] Invalid input: type={input_data.type}, value={input_data.value}")
                status = "invalid_input"
                raise ValueError(f"Invalid input: {input_data}")

            case_file = self._create_case_file(input_data)
            log.debug(f"[investigate] Created case_file: {case_file.id}")
            
            log.info(f"[investigate] Determining scope and time window...")
            await self._determine_scope_and_time_window(case_file, filters)
            log.info(
                f"[investigate] Scope determined | "
                f"serviceName={case_file.scope.serviceName} | "
                f"environment={case_file.scope.environment} | "
                f"additionalLabels={list((case_file.scope.additionalLabels or {}).keys())}"
            )

            log.info(f"[investigate] Gathering signals from MCP servers...")
            evidence_list = await self._gather_signals(case_file)
            log.info(f"[investigate] Gathered {len(evidence_list)} evidence items")

            log.info(f"[investigate] Correlating signals...")
            correlated_evidence, gaps = self.correlation_engine.correlate_signals(
                evidence_list, case_file.scope
            )
            case_file.evidence = correlated_evidence
            case_file.correlationGaps = gaps
            CORRELATION_GAPS.inc(len(gaps))
            log.info(
                f"[investigate] Correlation complete | "
                f"evidence={len(correlated_evidence)} | "
                f"gaps={len(gaps)}"
            )

            log.info(f"[investigate] Generating hypotheses...")
            case_file.hypotheses = self.hypothesis_generator.generate_hypotheses(
                correlated_evidence, case_file.scope
            )
            log.info(f"[investigate] Generated {len(case_file.hypotheses)} hypotheses")

            log.info(f"[investigate] Applying guardrails...")
            self._apply_guardrails(case_file)

            execution_time = time.time() - start_time
            case_file.auditTrail.append(
                AuditEntry(
                    timestamp=datetime.utcnow().isoformat(),
                    action="Investigation completed",
                    details={
                        "evidence_count": len(case_file.evidence),
                        "hypotheses_count": len(case_file.hypotheses),
                        "execution_time": execution_time,
                    },
                )
            )
            case_file.updatedAt = datetime.utcnow().isoformat()
            
            # Record metrics
            EVIDENCE_COUNT.labels(input_type=input_data.type).observe(len(case_file.evidence))
            HYPOTHESIS_COUNT.labels(input_type=input_data.type).observe(len(case_file.hypotheses))
            
            log.info(
                f"[investigate] Investigation completed | "
                f"case_file_id={case_file.id} | "
                f"execution_time={execution_time:.3f}s | "
                f"evidence={len(case_file.evidence)} | "
                f"hypotheses={len(case_file.hypotheses)}"
            )
            
            return case_file

        except Exception:
            if status == "success":
                status = "error"
            raise

        finally:
            duration = time.time() - start_time
            INVESTIGATION_DURATION.labels(input_type=input_data.type, status=status).observe(duration)
            INVESTIGATION_TOTAL.labels(input_type=input_data.type, status=status).inc()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _validate_input(self, input_data: Input) -> bool:
        if input_data.type == InputType.INCIDENT_ID:
            return bool(re.match(r"^INC\d+$", input_data.value))
        elif input_data.type in (InputType.ALERT_UID, InputType.SYMPTOM):
            return bool(input_data.value.strip())
        return False

    def _create_case_file(self, input_data: Input) -> CaseFile:
        now = datetime.utcnow().isoformat()
        return CaseFile(
            id=str(uuid.uuid4()),
            createdAt=now,
            updatedAt=now,
            input=input_data,
            scope=Scope(),
            timeWindow=TimeWindow(start="", end="", duration=""),
            signals=[],
            evidence=[],
            hypotheses=[],
            correlationGaps=[],
            auditTrail=[
                AuditEntry(
                    timestamp=now,
                    action="Investigation started",
                    details={"input_type": input_data.type, "input_value": input_data.value},
                )
            ],
        )

    def _default_time_window(self) -> TimeWindow:
        end = datetime.utcnow()
        start = end - timedelta(hours=1)
        return TimeWindow(start=start.isoformat(), end=end.isoformat(), duration="1h")

    async def _determine_scope_and_time_window(self, case_file: CaseFile, filters: dict = None):
        input_data = case_file.input
        filters = filters or {}
        
        log.debug(f"[_determine_scope_and_time_window] input_type={input_data.type}, filters={filters}")

        if input_data.type == InputType.ALERT_UID:
            log.info(f"[_determine_scope_and_time_window] Fetching alert details for UID: {input_data.value}")
            mcp_client = MCPClient("grafana", config.mcp_servers["grafana"].endpoint)
            agent = GrafanaAgent(mcp_client)
            
            try:
                evidence = await agent.fetch_alert_details(input_data.value)
                if evidence:
                    alert_labels = evidence.result.get("labels", {})
                    log.debug(f"[_determine_scope_and_time_window] Alert labels: {list(alert_labels.keys())}")
                    case_file.scope = Scope(
                        serviceName=alert_labels.get("application_service"),
                        environment=alert_labels.get("env"),
                        cluster=alert_labels.get("cluster"),
                        namespace=alert_labels.get("namespace"),
                        additionalLabels={
                            "alertname": alert_labels.get("alertname", ""),
                            "owner_squad": alert_labels.get("owner_squad", ""),
                            "owner_sre": alert_labels.get("owner_sre", ""),
                            "severidade": alert_labels.get("Severidade", ""),
                            "business_service": alert_labels.get("business_service", ""),
                            "business_domain": alert_labels.get("business_domain", ""),
                            "business_capability": alert_labels.get("business_capability", ""),
                            "grafana_folder": alert_labels.get("grafana_folder", ""),
                            "datasource": alert_labels.get("Datasource", ""),
                        },
                    )
                    case_file.timeWindow = self._default_time_window()
                    log.info(f"[_determine_scope_and_time_window] Scope from alert: serviceName={case_file.scope.serviceName}")
                else:
                    log.warning(f"[_determine_scope_and_time_window] No evidence returned for alert UID: {input_data.value}")
            finally:
                await mcp_client.close()

        elif input_data.type == InputType.SYMPTOM:
            log.info(f"[_determine_scope_and_time_window] Processing SYMPTOM: {input_data.value[:100]}...")
            # Use explicit filters if provided, otherwise try keyword extraction
            service_name = filters.get("application_service")
            environment = filters.get("env") or filters.get("environment")

            if not service_name:
                symptom = input_data.value.lower()
                if "api-gateway" in symptom or "api gateway" in symptom:
                    service_name = "api-gateway"
                    log.debug(f"[_determine_scope_and_time_window] Extracted service from symptom: api-gateway")
                elif "auth" in symptom:
                    service_name = "auth-service"
                    log.debug(f"[_determine_scope_and_time_window] Extracted service from symptom: auth-service")

            if not environment:
                symptom = input_data.value.lower()
                if "production" in symptom or "prod" in symptom:
                    environment = "production"
                    log.debug(f"[_determine_scope_and_time_window] Extracted environment from symptom: production")
                elif "staging" in symptom:
                    environment = "staging"
                    log.debug(f"[_determine_scope_and_time_window] Extracted environment from symptom: staging")

            case_file.scope = Scope(
                serviceName=service_name,
                environment=environment,
                additionalLabels={
                    k: v for k, v in filters.items()
                    if k not in ("application_service", "env", "environment")
                } or None,
            )
            case_file.timeWindow = self._default_time_window()
            log.info(
                f"[_determine_scope_and_time_window] Scope from symptom: "
                f"serviceName={service_name}, environment={environment}, "
                f"additionalLabels={list((case_file.scope.additionalLabels or {}).keys())}"
            )

        else:  # INCIDENT_ID
            log.info(f"[_determine_scope_and_time_window] Fetching incident: {input_data.value}")
            mcp_client = MCPClient("incidents-pg", config.mcp_servers["incidents-pg"].endpoint)
            agent = IncidentsAgent(mcp_client)
            
            try:
                evidence = await agent.fetch_incident(input_data.value)
                if evidence:
                    result = evidence.result
                    
                    # PRIORIDADE: Extrair application_service das labels do Grafana no description
                    # cmdb_ci_name nem sempre corresponde ao application_service real
                    grafana_labels = result.get("_grafana_labels", {})
                    app_svc = grafana_labels.get("application_service") or result.get("cmdb_ci_name")
                    
                    case_file.scope = Scope(
                        serviceName=app_svc,
                        additionalLabels={
                            "incident_number": result.get("number", ""),
                            "priority": result.get("priority", ""),
                            "category": result.get("category", ""),
                            "assignment_group": result.get("assignment_group_name", ""),
                            "cmdb_ci_name": result.get("cmdb_ci_name", ""),
                        },
                    )
                    
                    # Adicionar labels do Grafana ao scope se disponíveis
                    if grafana_labels:
                        for label_key in ("business_capability", "business_domain", "business_service",
                                          "owner_squad", "owner_sre"):
                            val = grafana_labels.get(label_key)
                            if val and case_file.scope.additionalLabels is not None:
                                case_file.scope.additionalLabels[label_key] = val
                    
                    case_file.evidence.append(evidence)
                    log.info(
                        f"[_determine_scope_and_time_window] Scope from incident: "
                        f"serviceName={app_svc} (cmdb_ci_name={result.get('cmdb_ci_name')}) | "
                        f"priority={result.get('priority')} | "
                        f"grafana_labels={'yes' if grafana_labels else 'no'}"
                    )
                else:
                    log.warning(f"[_determine_scope_and_time_window] No evidence returned for incident: {input_data.value}")
            finally:
                await mcp_client.close()
            case_file.timeWindow = self._default_time_window()

    async def _gather_signals(self, case_file: CaseFile) -> List[Evidence]:
        import time
        start_time = time.time()
        
        log.info(
            f"[_gather_signals] Starting signal gathering | "
            f"serviceName={case_file.scope.serviceName} | "
            f"environment={case_file.scope.environment} | "
            f"additionalLabels={list((case_file.scope.additionalLabels or {}).keys())}"
        )
        
        evidence_list: List[Evidence] = []

        grafana_client = MCPClient("grafana", config.mcp_servers["grafana"].endpoint)
        incidents_client = MCPClient("incidents-pg", config.mcp_servers["incidents-pg"].endpoint)
        vm_client = MCPClient("victoriametrics", config.mcp_servers["victoriametrics"].endpoint, timeout=30)
        grafana_agent = GrafanaAgent(grafana_client)
        incidents_agent = IncidentsAgent(incidents_client)
        metrics_agent = MetricsAgent(vm_client)

        try:
            # Always fetch firing alerts
            log.info(f"[_gather_signals] Fetching firing alerts from Grafana...")
            tasks = [grafana_agent.find_firing_alerts(case_file.scope)]

            # Fetch incidents if we have correlation keys
            ci_name = case_file.scope.serviceName
            additional = case_file.scope.additionalLabels or {}
            inc_number = additional.get("incident_number")
            
            log.info(
                f"[_gather_signals] Incident correlation keys | "
                f"application_service={ci_name} | "
                f"inc_number={inc_number}"
            )
            
            if inc_number or ci_name:
                log.info(
                    f"[_gather_signals] Fetching related incidents | "
                    f"number={inc_number} | "
                    f"application_service={ci_name}"
                )
                tasks.append(
                    incidents_agent.find_related_incidents(
                        number=inc_number, application_service=ci_name
                    )
                )
            else:
                log.warning(
                    f"[_gather_signals] Skipping incident search - no correlation keys | "
                    f"application_service={ci_name} | "
                    f"incident_number={inc_number}"
                )

            # Fetch metrics from VictoriaMetrics if we have a service name
            if ci_name and config.metrics_catalog:
                log.info(
                    f"[_gather_signals] Fetching catalog metrics from VictoriaMetrics | "
                    f"service={ci_name} | "
                    f"catalog_queries={len(config.metrics_catalog)}"
                )
                tasks.append(
                    metrics_agent.execute_catalog_queries(ci_name, config.metrics_catalog)
                )

            # Execute alert expression if we have alert data
            alert_data = self._extract_alert_data(case_file)
            if alert_data:
                log.info(f"[_gather_signals] Executing alert PromQL expression from alert data")
                tasks.append(
                    metrics_agent.execute_alert_expression(alert_data)
                )

            log.info(f"[_gather_signals] Executing {len(tasks)} parallel tasks...")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for idx, result in enumerate(results, 1):
                if isinstance(result, Exception):
                    log.error(
                        f"[_gather_signals] Task {idx}/{len(tasks)} failed | "
                        f"error_type={type(result).__name__} | "
                        f"error={str(result)[:200]}"
                    )
                elif isinstance(result, list):
                    log.info(
                        f"[_gather_signals] Task {idx}/{len(tasks)} returned list | "
                        f"items={len(result)}"
                    )
                    evidence_list.extend(result)
                elif result is not None:
                    log.info(f"[_gather_signals] Task {idx}/{len(tasks)} returned single evidence")
                    evidence_list.append(result)
                else:
                    log.warning(f"[_gather_signals] Task {idx}/{len(tasks)} returned None")
                    
        finally:
            await grafana_client.close()
            await incidents_client.close()
            await vm_client.close()

        execution_time = time.time() - start_time
        log.info(
            f"[_gather_signals] Signal gathering completed | "
            f"evidence_count={len(evidence_list)} | "
            f"execution_time={execution_time:.3f}s"
        )
        
        return evidence_list

    def _extract_alert_data(self, case_file: CaseFile) -> Optional[Dict]:
        """Extrai dados do alerta do CaseFile para executar a expressão PromQL."""
        if case_file.input.type != InputType.ALERT_UID:
            return None
        for evidence in case_file.evidence:
            if evidence.source == "grafana-mcp" and evidence.type == EvidenceType.ALERT_FIRING:
                result = evidence.result
                if "data" in result:
                    return result
        return None

    def _apply_guardrails(self, case_file: CaseFile):
        for evidence in case_file.evidence:
            if not self.guardrails.validate_evidence_traceability(evidence):
                log.warning(f"Evidence {evidence.id} failed traceability check")
        for hypothesis in case_file.hypotheses:
            for next_step in hypothesis.nextSteps:
                if not self.guardrails.validate_read_only(next_step):
                    log.warning(f"NextStep '{next_step.action}' failed read-only check")
                    next_step.readOnly = True


# ============================================================================
# FastAPI Endpoints
# ============================================================================

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "orchestrator", "version": "1.1.0"}


@app.get("/metrics")
async def metrics_endpoint():
    """Prometheus metrics endpoint."""
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from starlette.responses import Response
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/steering")
async def get_steering():
    return {
        "steering_files": list(config.steering_context.keys()),
        "standard_labels": config.standard_labels,
    }


@app.post("/investigate", response_model=InvestigateResponse)
async def investigate_endpoint(request: InvestigateRequest):
    try:
        input_data = Input(
            type=InputType(request.input_type),
            value=request.value,
            timestamp=datetime.utcnow().isoformat(),
            user=request.user,
        )
        orchestrator = Orchestrator()
        case_file = await orchestrator.investigate(input_data, filters=request.filters)

        return InvestigateResponse(
            caseFileId=case_file.id,
            scope=asdict(case_file.scope),
            timeWindow=asdict(case_file.timeWindow),
            evidence=[asdict(e) for e in case_file.evidence],
            hypotheses=[asdict(h) for h in case_file.hypotheses],
            correlationGaps=[asdict(g) for g in case_file.correlationGaps],
            executionTime=float(case_file.auditTrail[-1].details.get("execution_time", 0)),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception("Error during investigation")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@app.get("/casefile/{case_file_id}")
async def get_case_file(case_file_id: str):
    return {"error": "CaseFile storage not yet implemented", "case_file_id": case_file_id}


# ============================================================================
# Chat Endpoint (LLM-powered)
# ============================================================================

class ChatRequest(BaseModel):
    message: str = Field(..., description="User message in natural language")
    session_id: Optional[str] = Field(default=None, description="Session ID for conversation continuity")


class ChatResponse(BaseModel):
    response: str
    session_id: str


# Session store (in-memory for PoC)
_chat_sessions: Dict[str, Any] = {}


async def _execute_tool(tool_name: str, arguments: dict) -> dict:
    """Execute a tool via MCP servers. Used by LLM function calling."""
    import time
    start_time = time.time()
    
    log.info(
        f"[_execute_tool] Starting tool execution | "
        f"tool={tool_name} | "
        f"arguments={arguments}"
    )
    
    grafana_tools = {"find_firing_alerts", "get_alert_details", "find_dashboards", "get_panel_link"}
    incidents_tools = {"get_incident", "search_incidents", "get_related_incidents", "get_incident_stats"}
    vm_tools = {"query", "query_range", "metrics", "labels", "label_values", "series",
                "rules", "alerts", "tsdb_status", "top_queries", "active_queries",
                "metric_statistics", "documentation", "prettify_query", "explain_query",
                "metrics_metadata", "tenants"}
    
    if tool_name in grafana_tools:
        server_name = "grafana"
        endpoint = config.mcp_servers["grafana"].endpoint
        log.debug(f"[_execute_tool] Routing to Grafana MCP | endpoint={endpoint}")
        client = MCPClient(server_name, endpoint)
    elif tool_name in incidents_tools:
        server_name = "incidents-pg"
        endpoint = config.mcp_servers["incidents-pg"].endpoint
        log.debug(f"[_execute_tool] Routing to Incidents PG MCP | endpoint={endpoint}")
        client = MCPClient(server_name, endpoint)
    elif tool_name in vm_tools:
        server_name = "victoriametrics"
        endpoint = config.mcp_servers["victoriametrics"].endpoint
        log.debug(f"[_execute_tool] Routing to VictoriaMetrics MCP Proxy | endpoint={endpoint}")
        client = MCPClient(server_name, endpoint)
    else:
        log.error(f"[_execute_tool] Unknown tool: {tool_name}")
        return {"error": f"Unknown tool: {tool_name}"}

    try:
        log.info(f"[_execute_tool] Calling MCP server | server={server_name} | tool={tool_name}")
        result = await client.call_tool(tool_name, arguments)
        
        # Apply PII redaction
        from guardrails import Guardrails
        result_str, redacted = Guardrails.redact_pii(json.dumps(result, default=str))
        result = json.loads(result_str)
        if redacted:
            PII_REDACTIONS.inc()
        
        execution_time = time.time() - start_time
        MCP_CALL_DURATION.labels(server=server_name, tool=tool_name, status="success").observe(execution_time)
        MCP_CALL_TOTAL.labels(server=server_name, tool=tool_name, status="success").inc()
        log.info(
            f"[_execute_tool] Tool execution completed | "
            f"tool={tool_name} | "
            f"server={server_name} | "
            f"execution_time={execution_time:.3f}s | "
            f"result_size={len(result_str)} bytes | "
            f"pii_redacted={redacted}"
        )
        
        return result
    except Exception as e:
        execution_time = time.time() - start_time
        MCP_CALL_DURATION.labels(server=server_name, tool=tool_name, status="error").observe(execution_time)
        MCP_CALL_TOTAL.labels(server=server_name, tool=tool_name, status="error").inc()
        log.error(
            f"[_execute_tool] Tool execution failed | "
            f"tool={tool_name} | "
            f"server={server_name} | "
            f"execution_time={execution_time:.3f}s | "
            f"error_type={type(e).__name__} | "
            f"error={str(e)[:200]}"
        )
        return {"error": f"Tool execution failed: {str(e)}"}
    finally:
        await client.close()


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """Conversational endpoint powered by LLM with function calling."""
    import time
    start_time = time.time()
    
    log.info(
        f"[chat_endpoint] Received chat request | "
        f"message_length={len(request.message)} | "
        f"session_id={request.session_id or 'new'}"
    )
    
    try:
        from llm_client import LLMClient
        log.debug("[chat_endpoint] LLMClient imported successfully")
    except Exception as e:
        log.error(
            f"[chat_endpoint] Failed to import LLMClient | "
            f"error_type={type(e).__name__} | "
            f"error={str(e)}"
        )
        raise HTTPException(
            status_code=503,
            detail=f"LLM not configured. Set OPENAI_API_KEY env var. Error: {e}",
        )

    session_id = request.session_id or str(uuid.uuid4())
    log.debug(f"[chat_endpoint] Using session_id: {session_id}")

    # Get or create session
    if session_id not in _chat_sessions:
        log.info(f"[chat_endpoint] Creating new LLM session | session_id={session_id}")
        try:
            _chat_sessions[session_id] = LLMClient()
            CHAT_SESSIONS_ACTIVE.inc()
            log.info(f"[chat_endpoint] LLM session created | session_id={session_id}")
        except Exception as e:
            log.error(
                f"[chat_endpoint] Failed to create LLM session | "
                f"session_id={session_id} | "
                f"error_type={type(e).__name__} | "
                f"error={str(e)}"
            )
            raise HTTPException(
                status_code=503,
                detail=f"Failed to initialize LLM client: {str(e)}",
            )
    else:
        log.debug(f"[chat_endpoint] Reusing existing LLM session | session_id={session_id}")

    llm = _chat_sessions[session_id]

    try:
        log.info(f"[chat_endpoint] Starting LLM chat | session_id={session_id}")
        response_text = await llm.chat(
            user_message=request.message,
            tool_executor=_execute_tool,
        )
        
        execution_time = time.time() - start_time
        CHAT_DURATION.labels(status="success").observe(execution_time)
        CHAT_TOTAL.labels(status="success").inc()
        log.info(
            f"[chat_endpoint] Chat completed successfully | "
            f"session_id={session_id} | "
            f"execution_time={execution_time:.3f}s | "
            f"response_length={len(response_text)}"
        )
        
        return ChatResponse(response=response_text, session_id=session_id)
    except Exception as e:
        execution_time = time.time() - start_time
        CHAT_DURATION.labels(status="error").observe(execution_time)
        CHAT_TOTAL.labels(status="error").inc()
        log.error(
            f"[chat_endpoint] Chat failed | "
            f"session_id={session_id} | "
            f"execution_time={execution_time:.3f}s | "
            f"error_type={type(e).__name__} | "
            f"error={str(e)[:500]}"
        )
        log.exception("[chat_endpoint] Full exception details:")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    log.info("Starting Observability Troubleshooting Copilot Orchestrator")
    log.info(f"Loaded {len(config.steering_context)} steering files")
    log.info(f"MCP servers configured: {list(config.mcp_servers.keys())}")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        log_level="info",
    )
