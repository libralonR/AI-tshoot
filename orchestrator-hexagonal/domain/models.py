"""Modelos do domínio.

Idênticos aos da versão atual em `orchestrator/models.py`. São os contratos
de dados que use cases, ports e adapters compartilham.
"""

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InputType(str, Enum):
    INCIDENT_ID = "INCIDENT_ID"
    ALERT_UID = "ALERT_UID"
    SYMPTOM = "SYMPTOM"


class EvidenceType(str, Enum):
    METRIC_ANOMALY = "METRIC_ANOMALY"
    LOG_ERROR = "LOG_ERROR"
    TRACE_SLOW_SPAN = "TRACE_SLOW_SPAN"
    TRACE_ERROR = "TRACE_ERROR"
    ALERT_FIRING = "ALERT_FIRING"
    DASHBOARD_PANEL = "DASHBOARD_PANEL"
    INCIDENT_RELATED = "INCIDENT_RELATED"
    CHANGE_RECENT = "CHANGE_RECENT"


class Priority(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class Input:
    type: InputType
    value: str
    timestamp: str
    user: str


@dataclass
class Scope:
    serviceName: Optional[str] = None
    environment: Optional[str] = None
    cluster: Optional[str] = None
    namespace: Optional[str] = None
    pod: Optional[str] = None
    deployment: Optional[str] = None
    additionalLabels: Optional[Dict[str, str]] = None


@dataclass
class TimeWindow:
    start: str
    end: str
    duration: str


@dataclass
class Evidence:
    id: str
    type: EvidenceType
    source: str
    query: str
    result: Dict[str, Any]
    timestamp: str
    links: List[str]
    confidence: float
    redacted: bool


@dataclass
class NextStep:
    action: str
    description: str
    query: Optional[str] = None
    link: Optional[str] = None
    readOnly: bool = True
    priority: Priority = Priority.MEDIUM


@dataclass
class Hypothesis:
    id: str
    description: str
    suspectedComponent: str
    rootCause: str
    evidenceIds: List[str]
    confidence: float
    nextSteps: List[NextStep]


@dataclass
class CorrelationGap:
    missingLabel: str
    affectedSources: List[str]
    impact: str
    recommendation: str


@dataclass
class AuditEntry:
    timestamp: str
    action: str
    details: Dict[str, Any]


class CaseFile(BaseModel):
    """CaseFile canônico (mantém o mesmo schema da versão atual)."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
        extra="forbid",
    )

    id: str = Field(..., description="UUID v4")
    createdAt: str = Field(..., description="ISO 8601")
    updatedAt: str = Field(..., description="ISO 8601")
    input: Input
    scope: Scope
    timeWindow: TimeWindow
    signals: List[Dict[str, Any]] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    hypotheses: List[Hypothesis] = Field(default_factory=list)
    correlationGaps: List[CorrelationGap] = Field(default_factory=list)
    nextSteps: List[Dict[str, Any]] = Field(default_factory=list)
    auditTrail: List[AuditEntry] = Field(default_factory=list)
    redaction: Dict[str, Any] = Field(
        default_factory=lambda: {"applied": False, "patterns": [], "count": 0}
    )

    @field_validator("id")
    @classmethod
    def _validate_uuid(cls, value: str) -> str:
        try:
            UUID(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"CaseFile.id must be a valid UUID, got {value!r}") from exc
        return value

    @field_validator("createdAt", "updatedAt")
    @classmethod
    def _validate_iso8601(cls, value: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("Timestamp must be a non-empty ISO 8601 string")
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Timestamp {value!r} is not a valid ISO 8601 string") from exc
        return value

    def to_json_dict(self) -> Dict[str, Any]:
        def _convert(value: Any) -> Any:
            if is_dataclass(value):
                return asdict(value)
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, list):
                return [_convert(v) for v in value]
            if isinstance(value, dict):
                return {k: _convert(v) for k, v in value.items()}
            return value

        return {
            "id": self.id,
            "createdAt": self.createdAt,
            "updatedAt": self.updatedAt,
            "input": _convert(self.input),
            "scope": _convert(self.scope),
            "timeWindow": _convert(self.timeWindow),
            "signals": _convert(self.signals),
            "evidence": _convert(self.evidence),
            "hypotheses": _convert(self.hypotheses),
            "correlationGaps": _convert(self.correlationGaps),
            "nextSteps": _convert(self.nextSteps),
            "auditTrail": _convert(self.auditTrail),
            "redaction": _convert(self.redaction),
        }


# Schemas HTTP — fazem parte do contrato externo (driving adapter)
class InvestigateRequest(BaseModel):
    input_type: str = Field(..., description="INCIDENT_ID, ALERT_UID, or SYMPTOM")
    value: str = Field(..., description="Incident ID, Alert UID, or symptom description")
    user: str = Field(default="anonymous")
    filters: Optional[Dict[str, str]] = Field(default=None)


class InvestigateResponse(BaseModel):
    caseFileId: str
    scope: Dict[str, Any]
    timeWindow: Dict[str, Any]
    evidence: List[Dict[str, Any]]
    hypotheses: List[Dict[str, Any]]
    correlationGaps: List[Dict[str, Any]]
    executionTime: float
