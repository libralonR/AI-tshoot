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

from agents import GrafanaAgent, IncidentsAgent
from config import config
from correlation import CorrelationEngine
from guardrails import Guardrails
from hypothesis import HypothesisGenerator
from mcp_client import MCPClient
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
        self.hypothesis_generator = HypothesisGenerator()
        self.guardrails = Guardrails()

    async def investigate(self, input_data: Input, filters: dict = None) -> CaseFile:
        start_time = time.time()

        if not self._validate_input(input_data):
            raise ValueError(f"Invalid input: {input_data}")

        case_file = self._create_case_file(input_data)
        await self._determine_scope_and_time_window(case_file, filters)

        evidence_list = await self._gather_signals(case_file)

        correlated_evidence, gaps = self.correlation_engine.correlate_signals(
            evidence_list, case_file.scope
        )
        case_file.evidence = correlated_evidence
        case_file.correlationGaps = gaps

        case_file.hypotheses = self.hypothesis_generator.generate_hypotheses(
            correlated_evidence, case_file.scope
        )

        self._apply_guardrails(case_file)

        case_file.auditTrail.append(
            AuditEntry(
                timestamp=datetime.utcnow().isoformat(),
                action="Investigation completed",
                details={
                    "evidence_count": len(case_file.evidence),
                    "hypotheses_count": len(case_file.hypotheses),
                    "execution_time": time.time() - start_time,
                },
            )
        )
        case_file.updatedAt = datetime.utcnow().isoformat()
        return case_file

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

        if input_data.type == InputType.ALERT_UID:
            mcp_client = MCPClient("grafana", config.mcp_servers["grafana"].endpoint)
            agent = GrafanaAgent(mcp_client)
            evidence = await agent.fetch_alert_details(input_data.value)
            if evidence:
                alert_labels = evidence.result.get("labels", {})
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
            await mcp_client.close()

        elif input_data.type == InputType.SYMPTOM:
            # Use explicit filters if provided, otherwise try keyword extraction
            service_name = filters.get("application_service")
            environment = filters.get("env") or filters.get("environment")

            if not service_name:
                symptom = input_data.value.lower()
                if "api-gateway" in symptom or "api gateway" in symptom:
                    service_name = "api-gateway"
                elif "auth" in symptom:
                    service_name = "auth-service"

            if not environment:
                symptom = input_data.value.lower()
                if "production" in symptom or "prod" in symptom:
                    environment = "production"
                elif "staging" in symptom:
                    environment = "staging"

            case_file.scope = Scope(
                serviceName=service_name,
                environment=environment,
                additionalLabels={
                    k: v for k, v in filters.items()
                    if k not in ("application_service", "env", "environment")
                } or None,
            )
            case_file.timeWindow = self._default_time_window()

        else:  # INCIDENT_ID
            mcp_client = MCPClient("incidents-pg", config.mcp_servers["incidents-pg"].endpoint)
            agent = IncidentsAgent(mcp_client)
            evidence = await agent.fetch_incident(input_data.value)
            if evidence:
                result = evidence.result
                case_file.scope = Scope(
                    serviceName=result.get("cmdb_ci_name"),
                    additionalLabels={
                        "incident_number": result.get("number", ""),
                        "priority": result.get("priority", ""),
                        "category": result.get("category", ""),
                        "assignment_group": result.get("assignment_group_name", ""),
                    },
                )
                case_file.evidence.append(evidence)
            await mcp_client.close()
            case_file.timeWindow = self._default_time_window()

    async def _gather_signals(self, case_file: CaseFile) -> List[Evidence]:
        evidence_list: List[Evidence] = []

        grafana_client = MCPClient("grafana", config.mcp_servers["grafana"].endpoint)
        incidents_client = MCPClient("incidents-pg", config.mcp_servers["incidents-pg"].endpoint)
        grafana_agent = GrafanaAgent(grafana_client)
        incidents_agent = IncidentsAgent(incidents_client)

        try:
            tasks = [grafana_agent.find_firing_alerts(case_file.scope)]

            ci_name = case_file.scope.serviceName
            additional = case_file.scope.additionalLabels or {}
            inc_number = additional.get("incident_number")
            if inc_number or ci_name:
                tasks.append(
                    incidents_agent.find_related_incidents(
                        number=inc_number, cmdb_ci_name=ci_name
                    )
                )

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    log.error(f"Error gathering signals: {result}")
                elif isinstance(result, list):
                    evidence_list.extend(result)
                elif result is not None:
                    evidence_list.append(result)
        finally:
            await grafana_client.close()
            await incidents_client.close()

        return evidence_list

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
    return {"status": "healthy", "service": "orchestrator", "version": "1.0.0"}


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
    grafana_tools = {"find_firing_alerts", "get_alert_details", "find_dashboards", "get_panel_link"}
    incidents_tools = {"get_incident", "search_incidents", "get_related_incidents", "get_incident_stats"}
    if tool_name in grafana_tools:
        client = MCPClient("grafana", config.mcp_servers["grafana"].endpoint)
    elif tool_name in incidents_tools:
        client = MCPClient("incidents-pg", config.mcp_servers["incidents-pg"].endpoint)
    else:
        return {"error": f"Unknown tool: {tool_name}"}

    try:
        result = await client.call_tool(tool_name, arguments)
        # Apply PII redaction
        from guardrails import Guardrails
        result_str, _ = Guardrails.redact_pii(json.dumps(result, default=str))
        return json.loads(result_str)
    finally:
        await client.close()


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """Conversational endpoint powered by LLM with function calling."""
    try:
        from llm_client import LLMClient
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"LLM not configured. Set OPENAI_API_KEY env var. Error: {e}",
        )

    session_id = request.session_id or str(uuid.uuid4())

    # Get or create session
    if session_id not in _chat_sessions:
        _chat_sessions[session_id] = LLMClient()

    llm = _chat_sessions[session_id]

    try:
        response_text = await llm.chat(
            user_message=request.message,
            tool_executor=_execute_tool,
        )
        return ChatResponse(response=response_text, session_id=session_id)
    except Exception as e:
        log.exception("Error in chat")
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
