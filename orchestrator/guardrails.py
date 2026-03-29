"""Security and compliance guardrails."""

import logging
import re

from models import Evidence, NextStep

log = logging.getLogger("orchestrator")


class Guardrails:
    """Enforce security and compliance guardrails."""

    @staticmethod
    def redact_pii(text: str) -> tuple[str, bool]:
        """Redact PII from text. Returns (redacted_text, was_redacted)."""
        redacted = False

        if re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text):
            text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL_REDACTED]', text)
            redacted = True

        if re.search(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', text):
            text = re.sub(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', '[PHONE_REDACTED]', text)
            redacted = True

        if re.search(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', text):
            text = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[IP_REDACTED]', text)
            redacted = True

        if re.search(r'\b(?:sk-|glsa_|xox[bpas]-|ghp_|gho_|AKIA)[A-Za-z0-9_\-]{20,}\b', text):
            text = re.sub(r'\b(?:sk-|glsa_|xox[bpas]-|ghp_|gho_|AKIA)[A-Za-z0-9_\-]{20,}\b', '[API_KEY_REDACTED]', text)
            redacted = True

        return text, redacted

    @staticmethod
    def validate_read_only(next_step: NextStep) -> bool:
        if not next_step.readOnly:
            log.warning(f"NextStep '{next_step.action}' is not read-only!")
            return False
        mutation_keywords = ["delete", "update", "create", "restart", "scale", "rollback", "deploy"]
        action_lower = next_step.action.lower()
        for kw in mutation_keywords:
            if kw in action_lower:
                log.warning(f"NextStep '{next_step.action}' contains mutation keyword: {kw}")
                return False
        return True

    @staticmethod
    def validate_evidence_traceability(evidence: Evidence) -> bool:
        if not evidence.query and not evidence.links:
            log.warning(f"Evidence {evidence.id} lacks traceability (no query or links)")
            return False
        return True
