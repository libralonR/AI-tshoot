"""Rotas HTTP do orchestrator hexagonal.

Mantém compatibilidade total com a versão atual:
  GET  /health
  GET  /metrics
  GET  /steering
  POST /investigate
  GET  /casefile/{case_file_id}
  POST /chat

Os mesmos schemas (`InvestigateRequest`, `InvestigateResponse`, `ChatRequest`,
`ChatResponse`) são usados, garantindo que UI Streamlit e bots não percebam
diferença.
"""

import logging
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from starlette.responses import Response

from api.dependencies import (
    build_chat_use_case,
    build_investigate_use_case_context,
    execute_tool,
    get_case_file_repository,
    get_chat_registry,
)
from domain.models import (
    Input,
    InputType,
    InvestigateRequest,
    InvestigateResponse,
)
from infrastructure.config import config
from infrastructure.prometheus_metrics import (
    CHAT_DURATION,
    CHAT_SESSIONS_ACTIVE,
    CHAT_TOTAL,
    CORRELATION_GAPS,
    EVIDENCE_COUNT,
    HYPOTHESIS_COUNT,
    INVESTIGATION_DURATION,
    INVESTIGATION_TOTAL,
)

log = logging.getLogger("orchestrator")

router = APIRouter()


# ---------------------------------------------------------------------------
# Health / Metrics / Steering
# ---------------------------------------------------------------------------

@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "orchestrator",
        "version": "1.1.0-hex",
        "architecture": "hexagonal",
    }


@router.get("/metrics")
async def metrics_endpoint():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/steering")
async def get_steering():
    return {
        "steering_files": list(config.steering_context.keys()),
        "standard_labels": config.standard_labels,
    }


# ---------------------------------------------------------------------------
# Investigate
# ---------------------------------------------------------------------------

@router.post("/investigate", response_model=InvestigateResponse)
async def investigate_endpoint(request: InvestigateRequest):
    start_time = time.time()
    status = "success"
    input_type = request.input_type

    try:
        input_data = Input(
            type=InputType(request.input_type),
            value=request.value,
            timestamp=datetime.utcnow().isoformat(),
            user=request.user,
        )

        async with await build_investigate_use_case_context() as use_case:
            case_file = await use_case.execute(input_data, filters=request.filters)

        EVIDENCE_COUNT.labels(input_type=input_type).observe(len(case_file.evidence))
        HYPOTHESIS_COUNT.labels(input_type=input_type).observe(len(case_file.hypotheses))
        CORRELATION_GAPS.inc(len(case_file.correlationGaps))

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
        status = "invalid_input"
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        status = "error"
        raise
    except Exception as e:  # noqa: BLE001
        status = "error"
        log.exception("Error during investigation")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")
    finally:
        duration = time.time() - start_time
        INVESTIGATION_DURATION.labels(input_type=input_type, status=status).observe(duration)
        INVESTIGATION_TOTAL.labels(input_type=input_type, status=status).inc()


@router.get("/casefile/{case_file_id}")
async def get_case_file(case_file_id: str):
    repo = get_case_file_repository()
    case_file = await repo.get(case_file_id)
    if not case_file:
        raise HTTPException(status_code=404, detail=f"CaseFile {case_file_id} not found")
    return case_file.to_json_dict()


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(..., description="User message in natural language")
    session_id: Optional[str] = Field(default=None, description="Session ID for conversation continuity")


class ChatResponse(BaseModel):
    response: str
    session_id: str


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    start_time = time.time()
    log.info(
        f"[chat_endpoint] Received | message_length={len(request.message)} | "
        f"session_id={request.session_id or 'new'}"
    )

    session_id = request.session_id or str(uuid.uuid4())
    registry = get_chat_registry()

    try:
        is_new = not registry.has(session_id)

        def _factory():
            return build_chat_use_case()

        chat_use_case = registry.get_or_create(session_id, _factory)
        if is_new:
            CHAT_SESSIONS_ACTIVE.inc()

        response_text = await chat_use_case.execute(
            user_message=request.message,
            tool_executor=execute_tool,
        )

        execution_time = time.time() - start_time
        CHAT_DURATION.labels(status="success").observe(execution_time)
        CHAT_TOTAL.labels(status="success").inc()
        log.info(
            f"[chat_endpoint] OK | session_id={session_id} | "
            f"execution_time={execution_time:.3f}s | response_length={len(response_text)}"
        )
        return ChatResponse(response=response_text, session_id=session_id)

    except RuntimeError as e:
        # OPENAI_API_KEY ausente, gateway timeout etc. Mapear para 503.
        execution_time = time.time() - start_time
        CHAT_DURATION.labels(status="error").observe(execution_time)
        CHAT_TOTAL.labels(status="error").inc()
        log.error(
            f"[chat_endpoint] LLM unavailable | session_id={session_id} | "
            f"execution_time={execution_time:.3f}s | error={str(e)[:200]}"
        )
        raise HTTPException(status_code=503, detail=str(e))

    except Exception as e:  # noqa: BLE001
        execution_time = time.time() - start_time
        CHAT_DURATION.labels(status="error").observe(execution_time)
        CHAT_TOTAL.labels(status="error").inc()
        log.exception(f"[chat_endpoint] Chat failed | session_id={session_id}")
        raise HTTPException(status_code=500, detail=str(e))
