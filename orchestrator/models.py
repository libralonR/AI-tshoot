"""Data models for the Observability Troubleshooting Copilot."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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


@dataclass
class CaseFile:
    id: str
    createdAt: str
    updatedAt: str
    input: Input
    scope: Scope
    timeWindow: TimeWindow
    signals: List[Dict[str, Any]]
    evidence: List[Evidence]
    hypotheses: List[Hypothesis]
    correlationGaps: List[CorrelationGap]
    auditTrail: List[AuditEntry]


# Pydantic models for API
class InvestigateRequest(BaseModel):
    input_type: str = Field(..., description="INCIDENT_ID, ALERT_UID, or SYMPTOM")
    value: str = Field(..., description="Incident ID, Alert UID, or symptom description")
    user: str = Field(default="anonymous", description="User requesting investigation")
    filters: Optional[Dict[str, str]] = Field(
        default=None,
        description="Optional filters: application_service, owner_squad, severidade, business_capability, grafana_folder",
    )


class InvestigateResponse(BaseModel):
    caseFileId: str
    scope: Dict[str, Any]
    timeWindow: Dict[str, Any]
    evidence: List[Dict[str, Any]]
    hypotheses: List[Dict[str, Any]]
    correlationGaps: List[Dict[str, Any]]
    executionTime: float
