"""Hypothesis generation and ranking."""

import uuid
from typing import Any, Dict, List

from models import Evidence, EvidenceType, Hypothesis, NextStep, Priority, Scope


class HypothesisGenerator:
    """Generate and rank hypotheses from correlated evidence."""

    def generate_hypotheses(self, evidence_list: List[Evidence], scope: Scope) -> List[Hypothesis]:
        hypotheses = []
        component_evidence = self._group_by_component(evidence_list, scope)

        for component, evidences in component_evidence.items():
            confidence = 0.5
            confidence += min(0.3, len(evidences) * 0.1)
            if any("trace_id" in str(e.result) for e in evidences):
                confidence += 0.1
            if any(e.type == EvidenceType.ALERT_FIRING for e in evidences):
                confidence += 0.1
            confidence = min(1.0, confidence)

            hypothesis = Hypothesis(
                id=str(uuid.uuid4()),
                description=f"Issue detected in {component}",
                suspectedComponent=component,
                rootCause=self._infer_root_cause(evidences),
                evidenceIds=[e.id for e in evidences],
                confidence=confidence,
                nextSteps=self._generate_next_steps(component, evidences),
            )
            hypotheses.append(hypothesis)

        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        return hypotheses

    def _group_by_component(self, evidence_list: List[Evidence], scope: Scope) -> Dict[str, List[Evidence]]:
        groups: Dict[str, List[Evidence]] = {}
        for evidence in evidence_list:
            component = self._extract_component(evidence, scope)
            groups.setdefault(component, []).append(evidence)
        return groups

    def _extract_component(self, evidence: Evidence, scope: Scope) -> str:
        result = evidence.result

        if "labels" in result and "application_service" in result["labels"]:
            return result["labels"]["application_service"]
        if "correlation" in result and isinstance(result["correlation"], dict):
            app_svc = result["correlation"].get("application_service")
            if app_svc:
                return app_svc
        if "_grafana_labels" in result and isinstance(result["_grafana_labels"], dict):
            app_svc = result["_grafana_labels"].get("application_service")
            if app_svc:
                return app_svc
        if "cmdb_ci_name" in result and result["cmdb_ci_name"]:
            return result["cmdb_ci_name"]
        if "labels" in result and "service.name" in result["labels"]:
            return result["labels"]["service.name"]
        if "service" in result:
            return result["service"]

        return scope.serviceName or "unknown-service"

    def _infer_root_cause(self, evidences: List[Evidence]) -> str:
        types = [e.type for e in evidences]
        if EvidenceType.METRIC_ANOMALY in types and EvidenceType.LOG_ERROR in types:
            return "Resource saturation or application error causing failures"
        if EvidenceType.TRACE_ERROR in types and EvidenceType.LOG_ERROR in types:
            return "Application error with distributed trace evidence"
        if EvidenceType.ALERT_FIRING in types:
            return "Alert threshold breached, requires investigation"
        if EvidenceType.LOG_ERROR in types:
            return "Error pattern detected in logs"
        return "Anomaly detected, requires further investigation"

    def _generate_next_steps(self, component: str, evidences: List[Evidence]) -> List[NextStep]:
        steps = [
            NextStep(
                action="Check resource metrics",
                description=f"Review CPU, memory, and network metrics for {component}",
                query=f'rate(container_cpu_usage_seconds_total{{pod=~"{component}.*"}}[5m])',
                readOnly=True,
                priority=Priority.HIGH,
            )
        ]
        if any(e.type == EvidenceType.LOG_ERROR for e in evidences):
            steps.append(
                NextStep(
                    action="Review error logs",
                    description=f"Examine recent error logs for {component}",
                    query=f'index=app_logs service="{component}" level=ERROR',
                    readOnly=True,
                    priority=Priority.HIGH,
                )
            )
        if any(e.type in [EvidenceType.TRACE_ERROR, EvidenceType.TRACE_SLOW_SPAN] for e in evidences):
            steps.append(
                NextStep(
                    action="Analyze traces",
                    description=f"Review distributed traces for {component}",
                    query=f'{{service.name="{component}" && status=error}}',
                    readOnly=True,
                    priority=Priority.MEDIUM,
                )
            )
        steps.append(
            NextStep(
                action="Check recent changes",
                description=f"Review recent deployments or configuration changes for {component}",
                link=f"http://servicenow/changes?service={component}",
                readOnly=True,
                priority=Priority.MEDIUM,
            )
        )
        return steps
