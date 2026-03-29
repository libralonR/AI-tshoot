"""Signal correlation engine with label normalization."""

import logging
from typing import Any, Dict, List, Optional

from models import CorrelationGap, Evidence, Scope

log = logging.getLogger("orchestrator")


class CorrelationEngine:
    """Correlate signals across multiple sources using standard labels."""

    def __init__(self, standard_labels: List[str], label_aliases: Dict[str, str] = None):
        self.standard_labels = standard_labels
        self.label_aliases = label_aliases or {}

    def _normalize_labels(self, raw_labels: Dict[str, str]) -> Dict[str, str]:
        normalized = {}
        for key, value in raw_labels.items():
            canonical = self.label_aliases.get(key, key)
            if canonical not in normalized or not normalized[canonical]:
                normalized[canonical] = value
        return normalized

    def extract_correlation_key(self, evidence: Evidence) -> Optional[str]:
        raw_labels = self._extract_labels(evidence.result)
        labels = self._normalize_labels(raw_labels)
        key_parts = []
        for label in self.standard_labels:
            if label in labels:
                key_parts.append(f"{label}={labels[label]}")
        return "|".join(key_parts) if key_parts else None

    def _extract_labels(self, result: Dict[str, Any]) -> Dict[str, str]:
        labels = {}

        if "labels" in result:
            labels.update(result["labels"])

        if "result" in result and isinstance(result["result"], dict):
            if "labels" in result["result"]:
                labels.update(result["result"]["labels"])

        if "correlation" in result and isinstance(result["correlation"], dict):
            for k, v in result["correlation"].items():
                if v is not None:
                    labels[k] = str(v)

        if "_grafana_labels" in result and isinstance(result["_grafana_labels"], dict):
            for k, v in result["_grafana_labels"].items():
                if v is not None:
                    labels[k] = str(v)

        if "_parsed" in result and isinstance(result["_parsed"], dict):
            uid = result["_parsed"].get("alert_rule_uid")
            if uid:
                labels["alert_rule_uid"] = uid

        for label in self.standard_labels:
            if label in result:
                labels[label] = str(result[label])

        if "cmdb_ci_name" in result and result["cmdb_ci_name"]:
            labels["cmdb_ci_name"] = str(result["cmdb_ci_name"])
        if "assignment_group_name" in result and result["assignment_group_name"]:
            labels["assignment_group_name"] = str(result["assignment_group_name"])
        if "priority" in result and result["priority"]:
            labels["priority"] = str(result["priority"])

        return labels

    def correlate_signals(
        self, evidence_list: List[Evidence], scope: Scope
    ) -> tuple[List[Evidence], List[CorrelationGap]]:
        correlated_evidence = []
        gaps = []
        evidence_groups: Dict[str, List[Evidence]] = {}

        for evidence in evidence_list:
            correlation_key = self.extract_correlation_key(evidence)
            if correlation_key:
                evidence_groups.setdefault(correlation_key, []).append(evidence)
            else:
                missing_labels = self._find_missing_labels(evidence)
                gaps.append(
                    CorrelationGap(
                        missingLabel=", ".join(missing_labels),
                        affectedSources=[evidence.source],
                        impact="Cannot correlate with other signals",
                        recommendation=f"Add {', '.join(missing_labels)} to {evidence.source} output",
                    )
                )
                evidence.confidence *= 0.5
                correlated_evidence.append(evidence)

        for key, group in evidence_groups.items():
            if len(group) >= 2:
                for ev in group:
                    ev.confidence = min(1.0, ev.confidence * 1.2)
            else:
                group[0].confidence = min(1.0, group[0].confidence * 0.8)
            correlated_evidence.extend(group)

        return correlated_evidence, gaps

    def _find_missing_labels(self, evidence: Evidence) -> List[str]:
        raw_labels = self._extract_labels(evidence.result)
        normalized = self._normalize_labels(raw_labels)
        return [label for label in self.standard_labels if label not in normalized]
