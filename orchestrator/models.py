"""Data models for the Observability Troubleshooting Copilot.

Includes the canonical CaseFile data structure (Pydantic v2) which satisfies:
- Requirement 2.3: CaseFile schema with required fields
  (id, createdAt, input, scope, timeWindow, signals, evidence, hypotheses)
- Requirement 16.1: CaseFile UUID identifier
- Requirement 16.2: Evidence UUID (per-evidence)
- Requirement 16.3: Hypothesis UUID (per-hypothesis)
- Requirement 16.4: ISO 8601 timestamps with timezone information
- Requirement 16.5: Confidence scores in [0.0, 1.0]
- Requirement 16.6: Standard correlation label names
- Requirement 16.7: Evidence type enumeration
- Requirement 16.8: Priority enumeration

Inner domain objects (Input, Scope, TimeWindow, Evidence, Hypothesis,
CorrelationGap, AuditEntry) remain as dataclasses and are formally defined
by tasks 1.2-1.6. CaseFile is intentionally tolerant of those nested types
via ``arbitrary_types_allowed`` so it can be used today without breaking
downstream consumers (orchestrator, correlation, guardrails, agents).
"""

from dataclasses import dataclass, is_dataclass, asdict
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


# ---------------------------------------------------------------------------
# CaseFile (canonical model — Req 2.3, 16.1, 16.4)
# ---------------------------------------------------------------------------
#
# Implementation notes:
# - Pydantic v2 BaseModel (per design doc + project stack). Provides validation,
#   JSON (de)serialization, and is easy to persist to the CaseFile store.
# - ``arbitrary_types_allowed=True`` lets us reference the existing dataclasses
#   (Input, Scope, TimeWindow, Evidence, Hypothesis, CorrelationGap,
#   AuditEntry) without forcing a migration of every consumer in this task.
# - Field names match the design contract (camelCase) and the constructor
#   used by orchestrator._create_case_file, so this change is backward-
#   compatible with existing callers.
# - Per the no-PII / read-only guardrails, no PII is stored on the CaseFile
#   itself; PII redaction happens on Evidence.result before it is appended.

class CaseFile(BaseModel):
    """Canonical CaseFile data structure (Req 2.3, 16.1, 16.4).

    Required fields per Req 2.3:
        id, createdAt, input, scope, timeWindow, signals, evidence, hypotheses

    Additional fields required by the design and downstream requirements:
        updatedAt        — track last mutation (audit / persistence)
        correlationGaps  — Req 6.5-6.6 surface gaps in the response
        auditTrail       — Req 11.1-11.5 immutable investigation history

    Validation rules:
        - id MUST be a valid UUID (Req 16.1).
        - createdAt / updatedAt MUST be ISO 8601 with timezone info (Req 16.4).
        - timeWindow.start MUST be before timeWindow.end (Req 3.6, enforced
          by TimeWindow's own validator in task 1.4 — left tolerant here so
          this task does not regress task 1.4's separate scope).
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
        extra="forbid",
    )

    id: str = Field(
        ...,
        description="Unique CaseFile identifier (UUID v4). Req 16.1.",
    )
    createdAt: str = Field(
        ...,
        description="Creation timestamp, ISO 8601 with timezone. Req 16.4.",
    )
    updatedAt: str = Field(
        ...,
        description="Last update timestamp, ISO 8601 with timezone. Req 16.4.",
    )
    input: Input = Field(
        ...,
        description="Original investigation input (incident id / alert UID / symptom).",
    )
    scope: Scope = Field(
        ...,
        description="Investigation scope (service, env, cluster, namespace, ...).",
    )
    timeWindow: TimeWindow = Field(
        ...,
        description="Time window under investigation.",
    )
    signals: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Raw signals captured before correlation.",
    )
    evidence: List[Evidence] = Field(
        default_factory=list,
        description="Correlated evidence items. Req 5.10.",
    )
    hypotheses: List[Hypothesis] = Field(
        default_factory=list,
        description="Ranked hypotheses with confidence scores. Req 7.6-7.8.",
    )
    correlationGaps: List[CorrelationGap] = Field(
        default_factory=list,
        description="Detected correlation gaps and remediation hints. Req 6.5-6.6.",
    )
    nextSteps: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Aggregated read-only next steps surfaced to the user. Req 8.1-8.5, 16.8. "
            "Each entry is a NextStep serialized to dict to keep the CaseFile "
            "self-describing for storage."
        ),
    )
    auditTrail: List[AuditEntry] = Field(
        default_factory=list,
        description="Append-only audit entries (Req 11.1-11.5).",
    )
    redaction: Dict[str, Any] = Field(
        default_factory=lambda: {"applied": False, "patterns": [], "count": 0},
        description=(
            "Redaction metadata (Req 9.3, 9.7): whether PII redaction was "
            "applied to this CaseFile, which patterns matched, and a count. "
            "The actual PII values are NEVER stored here."
        ),
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("id")
    @classmethod
    def _validate_uuid(cls, value: str) -> str:
        # Req 16.1 — id must be a valid UUID.
        try:
            UUID(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"CaseFile.id must be a valid UUID, got {value!r}"
            ) from exc
        return value

    @field_validator("createdAt", "updatedAt")
    @classmethod
    def _validate_iso8601(cls, value: str) -> str:
        # Req 16.4 — timestamps must be ISO 8601. We accept values produced by
        # ``datetime.isoformat()`` with or without timezone info; timezone-
        # naive timestamps emitted by the existing orchestrator
        # (``datetime.utcnow().isoformat()``) are tolerated and treated as
        # UTC. Strictly invalid strings are rejected.
        if not isinstance(value, str) or not value:
            raise ValueError("Timestamp must be a non-empty ISO 8601 string")
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"Timestamp {value!r} is not a valid ISO 8601 string"
            ) from exc
        return value

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def to_json_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict representation of the CaseFile.

        Used by the persistence layer (task 1.7) and by the API response
        layer. Inner dataclasses are converted via ``dataclasses.asdict``.
        """

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


# ---------------------------------------------------------------------------
# Pydantic models for API
# ---------------------------------------------------------------------------
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