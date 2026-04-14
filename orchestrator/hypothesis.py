"""Hypothesis generation and ranking."""

import uuid
from typing import Any, Dict, List, Optional

from models import Evidence, EvidenceType, Hypothesis, NextStep, Priority, Scope


class HypothesisGenerator:
    """Generate and rank hypotheses from correlated evidence."""

    def __init__(self, metrics_catalog: List[Dict[str, str]] = None):
        self.metrics_catalog = metrics_catalog or []

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
        if EvidenceType.ALERT_FIRING in types and EvidenceType.METRIC_ANOMALY in types:
            return "Alert threshold breached with metric anomaly confirmed"
        if EvidenceType.ALERT_FIRING in types:
            return "Alert threshold breached, requires investigation"
        if EvidenceType.METRIC_ANOMALY in types:
            return "Metric anomaly detected"
        if EvidenceType.LOG_ERROR in types:
            return "Error pattern detected in logs"
        return "Anomaly detected, requires further investigation"

    def _generate_next_steps(self, component: str, evidences: List[Evidence]) -> List[NextStep]:
        steps: List[NextStep] = []

        # 1. KB link do ServiceNow (se disponível no alerta)
        kb_link = self._extract_kb_link(evidences)
        if kb_link:
            kb_id = kb_link.get("kb", "")
            steps.append(
                NextStep(
                    action=f"Consultar KB {kb_id}",
                    description=f"Artigo de troubleshooting no ServiceNow para {component}",
                    link=kb_link.get("kb_link"),
                    readOnly=True,
                    priority=Priority.HIGH,
                )
            )

        # 2. Golden Signals do catálogo
        golden_steps = self._golden_signal_steps(component)
        steps.extend(golden_steps)

        # 3. Infraestrutura do catálogo
        infra_steps = self._infrastructure_steps(component)
        steps.extend(infra_steps)

        # 4. Logs (se houver evidência de erro em logs)
        if any(e.type == EvidenceType.LOG_ERROR for e in evidences):
            steps.append(
                NextStep(
                    action="Analisar error logs",
                    description=f"Examinar logs de erro recentes de {component}",
                    query=f'index=app_logs service="{component}" level=ERROR',
                    readOnly=True,
                    priority=Priority.HIGH,
                )
            )

        # 5. Traces (se houver evidência de trace)
        if any(e.type in [EvidenceType.TRACE_ERROR, EvidenceType.TRACE_SLOW_SPAN] for e in evidences):
            steps.append(
                NextStep(
                    action="Analisar traces",
                    description=f"Revisar traces distribuídos de {component}",
                    query=f'{{service.name="{component}" && status=error}}',
                    readOnly=True,
                    priority=Priority.MEDIUM,
                )
            )

        return steps

    def _extract_kb_link(self, evidences: List[Evidence]) -> Optional[Dict[str, str]]:
        """Extrai KB link do ServiceNow das evidências de alertas."""
        for e in evidences:
            if e.type == EvidenceType.ALERT_FIRING:
                snow = e.result.get("servicenow", {})
                if snow and snow.get("kb_link"):
                    return snow
        return None

    def _golden_signal_steps(self, component: str) -> List[NextStep]:
        """Gera nextSteps baseados nos golden signals do catálogo."""
        steps = []

        # Agrupar por subcategoria para gerar steps mais úteis
        latency = [e for e in self.metrics_catalog if e.get("category") == "golden_signal" and "latency" in e.get("name", "")]
        errors = [e for e in self.metrics_catalog if e.get("category") == "golden_signal" and "error" in e.get("name", "")]
        traffic = [e for e in self.metrics_catalog if e.get("category") == "golden_signal" and "request_rate" in e.get("name", "")]
        saturation = [e for e in self.metrics_catalog if e.get("category") == "golden_signal" and e.get("name", "") in ("cpu_usage", "memory_usage", "memory_usage_percent")]

        if errors:
            entry = errors[0]  # error_rate
            query = entry["query_template"].replace("{service}", component)
            steps.append(
                NextStep(
                    action="Verificar taxa de erros",
                    description=f"Error rate HTTP 5xx de {component}",
                    query=query,
                    readOnly=True,
                    priority=Priority.HIGH,
                )
            )

        if latency:
            entry = latency[0]  # request_latency_p99
            query = entry["query_template"].replace("{service}", component)
            steps.append(
                NextStep(
                    action="Verificar latência P99",
                    description=f"Latência P99 das requisições de {component}",
                    query=query,
                    readOnly=True,
                    priority=Priority.HIGH,
                )
            )

        if traffic:
            entry = traffic[0]  # request_rate
            query = entry["query_template"].replace("{service}", component)
            steps.append(
                NextStep(
                    action="Verificar throughput",
                    description=f"Taxa de requisições por segundo de {component}",
                    query=query,
                    readOnly=True,
                    priority=Priority.MEDIUM,
                )
            )

        if saturation:
            for entry in saturation:
                query = entry["query_template"].replace("{service}", component)
                steps.append(
                    NextStep(
                        action=f"Verificar {entry.get('description', entry['name'])}",
                        description=f"{entry.get('description', '')} de {component}",
                        query=query,
                        readOnly=True,
                        priority=Priority.MEDIUM,
                    )
                )

        return steps

    def _infrastructure_steps(self, component: str) -> List[NextStep]:
        """Gera nextSteps de infraestrutura do catálogo."""
        steps = []
        infra = [e for e in self.metrics_catalog if e.get("category") == "infrastructure"]

        for entry in infra:
            query = entry["query_template"].replace("{service}", component)
            steps.append(
                NextStep(
                    action=f"Verificar {entry.get('description', entry['name'])}",
                    description=f"{entry.get('description', '')} de {component}",
                    query=query,
                    readOnly=True,
                    priority=Priority.MEDIUM,
                )
            )

        return steps
