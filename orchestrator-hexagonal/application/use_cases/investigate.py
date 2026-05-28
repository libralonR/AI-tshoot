"""Caso de uso: investigar um sintoma/alerta/incidente.

Equivalente ao `Orchestrator.investigate()` da versão atual, mas com:
- ports injetados via construtor (testável sem rede)
- lógica desacoplada de framework HTTP
- métricas Prometheus disparadas no driving adapter (api/), não aqui
"""

import asyncio
import logging
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from application.ports import (
    AlertSource,
    CaseFileRepository,
    IncidentSource,
    MetricSource,
    TraceSource,
)
from domain.correlation import CorrelationEngine
from domain.guardrails import Guardrails
from domain.hypothesis import HypothesisGenerator
from domain.models import (
    AuditEntry,
    CaseFile,
    Evidence,
    EvidenceType,
    Input,
    InputType,
    Scope,
    TimeWindow,
)

log = logging.getLogger("orchestrator")


class InvestigateUseCase:
    """Coordena uma investigação completa.

    Os ports concretos (alertas, incidentes, métricas, repositório) são
    injetados pelo container de DI em `api/dependencies.py`.
    """

    def __init__(
        self,
        alert_source: AlertSource,
        incident_source: IncidentSource,
        metric_source: MetricSource,
        correlation_engine: CorrelationEngine,
        hypothesis_generator: HypothesisGenerator,
        guardrails: Guardrails,
        metrics_catalog: List[Dict[str, str]],
        trace_source: Optional[TraceSource] = None,
        traces_catalog: Optional[List[Dict[str, Any]]] = None,
        splunk_source: Optional[Any] = None,
        logs_parquet_source: Optional[Any] = None,
        logs_catalog: Optional[List[Dict[str, Any]]] = None,
        case_file_repository: Optional[CaseFileRepository] = None,
    ):
        self.alerts = alert_source
        self.incidents = incident_source
        self.metrics = metric_source
        self.traces = trace_source
        self.splunk = splunk_source
        self.logs_parquet = logs_parquet_source
        self.correlation_engine = correlation_engine
        self.hypothesis_generator = hypothesis_generator
        self.guardrails = guardrails
        self.metrics_catalog = metrics_catalog
        self.traces_catalog = traces_catalog or []
        self.logs_catalog = logs_catalog or []
        self.repo = case_file_repository

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(self, input_data: Input, filters: Optional[dict] = None) -> CaseFile:
        start_time = time.time()
        log.info(
            f"[investigate] Starting investigation | "
            f"type={input_data.type} | value={input_data.value[:50]}... | "
            f"user={input_data.user} | filters={filters}"
        )

        if not self._validate_input(input_data):
            log.error(f"[investigate] Invalid input: type={input_data.type}, value={input_data.value}")
            raise ValueError(f"Invalid input: {input_data}")

        case_file = self._create_case_file(input_data)

        await self._determine_scope_and_time_window(case_file, filters or {})

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

        if self.repo:
            try:
                await self.repo.save(case_file)
            except Exception as exc:  # noqa: BLE001 — não deve quebrar resposta
                log.warning(f"[investigate] Failed to persist CaseFile: {exc}")

        log.info(
            f"[investigate] Investigation completed | case_file_id={case_file.id} | "
            f"execution_time={execution_time:.3f}s | evidence={len(case_file.evidence)} | "
            f"hypotheses={len(case_file.hypotheses)}"
        )
        return case_file

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _validate_input(self, input_data: Input) -> bool:
        if input_data.type == InputType.INCIDENT_ID:
            return bool(re.match(r"^INC\d+$", input_data.value))
        if input_data.type in (InputType.ALERT_UID, InputType.SYMPTOM):
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
                    details={
                        "input_type": input_data.type,
                        "input_value": input_data.value,
                    },
                )
            ],
        )

    def _default_time_window(self) -> TimeWindow:
        end = datetime.utcnow()
        start = end - timedelta(hours=1)
        return TimeWindow(start=start.isoformat(), end=end.isoformat(), duration="1h")

    def _time_window_from_timestamp(self, reference_time: str, margin_before_min: int = 30) -> TimeWindow:
        """Cria time window baseada em um timestamp de referência (opened_at, startsAt)."""
        try:
            ref = datetime.fromisoformat(reference_time.replace("Z", "+00:00"))
            if ref.tzinfo:
                ref = ref.replace(tzinfo=None)
        except (ValueError, TypeError):
            return self._default_time_window()

        start = ref - timedelta(minutes=margin_before_min)
        end = datetime.utcnow()

        if (end - start) > timedelta(hours=24):
            start = end - timedelta(hours=6)
            log.warning(
                f"[_time_window_from_timestamp] Reference too old ({reference_time}), "
                f"capping window to last 6h"
            )

        duration_h = (end - start).total_seconds() / 3600
        return TimeWindow(
            start=start.isoformat(),
            end=end.isoformat(),
            duration=f"{duration_h:.1f}h",
        )

    async def _determine_scope_and_time_window(
        self, case_file: CaseFile, filters: dict
    ) -> None:
        input_data = case_file.input

        if input_data.type == InputType.ALERT_UID:
            evidence = await self.alerts.fetch_alert_details(input_data.value)
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
                case_file.evidence.append(evidence)
                # Time window dinâmica: usa startsAt do alerta
                starts_at = alert_labels.get("startsAt") or evidence.result.get("startsAt")
                if starts_at:
                    case_file.timeWindow = self._time_window_from_timestamp(starts_at)
                else:
                    case_file.timeWindow = self._default_time_window()
            else:
                case_file.timeWindow = self._default_time_window()

        elif input_data.type == InputType.SYMPTOM:
            service_name = filters.get("application_service")
            environment = filters.get("env") or filters.get("environment")

            if not service_name:
                symptom = input_data.value.lower()
                if "api-gateway" in symptom or "api gateway" in symptom:
                    service_name = "api-gateway"
                elif "auth" in symptom:
                    service_name = "auth-service"

            if not service_name:
                log.warning(
                    f"[_determine_scope_and_time_window] SYMPTOM without application_service — "
                    f"investigation will be limited (no metrics/traces/incidents correlation). "
                    f"Consider passing filters.application_service for better results."
                )

            if not environment:
                symptom = input_data.value.lower()
                if "production" in symptom or "prod" in symptom:
                    environment = "production"
                elif "staging" in symptom:
                    environment = "staging"

            extra = {
                k: v for k, v in filters.items()
                if k not in ("application_service", "env", "environment")
            }
            case_file.scope = Scope(
                serviceName=service_name,
                environment=environment,
                additionalLabels=extra or None,
            )
            case_file.timeWindow = self._default_time_window()

        else:  # INCIDENT_ID
            evidence = await self.incidents.fetch_incident(input_data.value)
            if evidence:
                result = evidence.result
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
                if grafana_labels:
                    for label_key in (
                        "business_capability",
                        "business_domain",
                        "business_service",
                        "owner_squad",
                        "owner_sre",
                    ):
                        val = grafana_labels.get(label_key)
                        if val and case_file.scope.additionalLabels is not None:
                            case_file.scope.additionalLabels[label_key] = val
                case_file.evidence.append(evidence)
                # Time window dinâmica: usa opened_at do incidente
                opened_at = result.get("opened_at")
                if opened_at:
                    case_file.timeWindow = self._time_window_from_timestamp(opened_at)
                else:
                    case_file.timeWindow = self._default_time_window()
            else:
                case_file.timeWindow = self._default_time_window()

    async def _gather_signals(self, case_file: CaseFile) -> List[Evidence]:
        ci_name = case_file.scope.serviceName
        additional = case_file.scope.additionalLabels or {}
        inc_number = additional.get("incident_number")
        has_catalog = bool(ci_name and self.metrics_catalog)

        log.info(
            f"[_gather_signals] Starting | serviceName={ci_name} | "
            f"environment={case_file.scope.environment} | "
            f"has_incident_number={bool(inc_number)} | "
            f"has_metrics_catalog={has_catalog}"
        )

        evidence_list: List[Evidence] = []
        tasks = []
        task_names = []

        task_names.append("alerts:find_firing_alerts")
        tasks.append(self.alerts.find_firing_alerts(case_file.scope))

        if inc_number or ci_name:
            task_names.append("incidents:find_related_incidents")
            tasks.append(
                self.incidents.find_related_incidents(
                    number=inc_number, application_service=ci_name
                )
            )

        if has_catalog:
            task_names.append("metrics:execute_catalog_queries")
            tasks.append(
                self.metrics.execute_catalog_queries(ci_name, self.metrics_catalog)
            )

        alert_data = self._extract_alert_data(case_file)
        if alert_data:
            task_names.append("metrics:execute_alert_expression")
            tasks.append(self.metrics.execute_alert_expression(alert_data))

        # Traces catalog (se houver service + traces_catalog + adapter de traces)
        has_traces_catalog = bool(ci_name and self.traces and self.traces_catalog)
        if has_traces_catalog:
            task_names.append("traces:execute_catalog_queries")
            tasks.append(
                self.traces.execute_catalog_queries(
                    service_name=ci_name,
                    catalog=self.traces_catalog,
                    time_window_start=case_file.timeWindow.start or None,
                    time_window_end=case_file.timeWindow.end or None,
                )
            )

        # trace_id direto do alerta (atalho para puxar trace específico)
        alert_trace_id = self._extract_alert_trace_id(case_file)
        if alert_trace_id and self.traces:
            task_names.append("traces:fetch_trace_id_from_alert")
            tasks.append(self.traces.fetch_trace_id_from_alert(alert_trace_id))

        # Splunk error patterns (se tiver service + adapter)
        if ci_name and self.splunk:
            task_names.append("splunk:find_error_patterns")
            tasks.append(
                self.splunk.find_error_patterns(
                    application_service=ci_name,
                    start=case_file.timeWindow.start or "-1h",
                    end=case_file.timeWindow.end or "now",
                    top_n=5,
                )
            )

        # Logs Parquet error patterns (se tiver service + adapter)
        if ci_name and self.logs_parquet:
            task_names.append("logs_parquet:find_error_patterns")
            bcap = additional.get("business_capability")
            tasks.append(
                self.logs_parquet.find_error_patterns(
                    application_service=ci_name,
                    start=case_file.timeWindow.start or "",
                    end=case_file.timeWindow.end or "",
                    business_capability=bcap,
                    top_n=5,
                )
            )

        log.info(f"[_gather_signals] Executing {len(tasks)} parallel tasks: {task_names}")
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for idx, result in enumerate(results):
            name = task_names[idx] if idx < len(task_names) else f"task_{idx}"
            if isinstance(result, Exception):
                log.error(
                    f"[_gather_signals] FAILED {name} | "
                    f"error_type={type(result).__name__} | error={str(result)[:200]}"
                )
            elif isinstance(result, list):
                log.info(f"[_gather_signals] OK {name} | evidence_count={len(result)}")
                evidence_list.extend(result)
            elif result is not None:
                log.info(f"[_gather_signals] OK {name} | evidence_count=1")
                evidence_list.append(result)
            else:
                log.warning(f"[_gather_signals] EMPTY {name}")

        by_source: Dict[str, int] = {}
        for e in evidence_list:
            by_source[e.source] = by_source.get(e.source, 0) + 1
        log.info(
            f"[_gather_signals] Done | total_evidence={len(evidence_list)} | by_source={by_source}"
        )
        return evidence_list

    def _extract_alert_data(self, case_file: CaseFile) -> Optional[Dict[str, Any]]:
        if case_file.input.type != InputType.ALERT_UID:
            return None
        for evidence in case_file.evidence:
            if (
                evidence.source == "grafana-mcp"
                and evidence.type == EvidenceType.ALERT_FIRING
            ):
                if "data" in evidence.result:
                    return evidence.result
        return None

    def _extract_alert_trace_id(self, case_file: CaseFile) -> Optional[str]:
        """Procura trace_id no alerta para puxar trace direto via Tempo."""
        if case_file.input.type != InputType.ALERT_UID:
            return None
        candidates = ("trace_id", "traceID", "traceId")
        for evidence in case_file.evidence:
            if (
                evidence.source != "grafana-mcp"
                or evidence.type != EvidenceType.ALERT_FIRING
            ):
                continue
            result = evidence.result
            for key in candidates:
                if result.get(key):
                    return str(result[key])
            for bag_key in ("labels", "annotations", "_parsed"):
                bag = result.get(bag_key)
                if isinstance(bag, dict):
                    for key in candidates:
                        if bag.get(key):
                            return str(bag[key])
        return None

    def _apply_guardrails(self, case_file: CaseFile) -> None:
        for evidence in case_file.evidence:
            if not self.guardrails.validate_evidence_traceability(evidence):
                log.warning(f"Evidence {evidence.id} failed traceability check")
        for hypothesis in case_file.hypotheses:
            for next_step in hypothesis.nextSteps:
                if not self.guardrails.validate_read_only(next_step):
                    log.warning(f"NextStep '{next_step.action}' failed read-only check")
                    next_step.readOnly = True
