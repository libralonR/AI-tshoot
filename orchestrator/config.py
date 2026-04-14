"""Configuration for the Observability Troubleshooting Copilot."""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

log = logging.getLogger("orchestrator")

STEERING_DIR = Path(__file__).parent / "steering"
PROMPTS_DIR = Path(__file__).parent / "prompts"
SPECS_DIR = Path(__file__).parent / "specs"


@dataclass
class MCPServerConfig:
    endpoint: str
    timeout: int = 15


class Config:
    """Global configuration loaded from environment and files."""

    def __init__(self):
        self.mcp_servers = {
            "grafana": MCPServerConfig(
                endpoint=os.getenv(
                    "GRAFANA_MCP_ENDPOINT",
                    "http://grafana-mcp-server.observability.svc.cluster.local:8080",
                ),
                timeout=15,
            ),
            "victoriametrics": MCPServerConfig(
                endpoint=os.getenv(
                    "VM_MCP_ENDPOINT",
                    "http://vm-mcp-proxy.observability.svc.cluster.local:8084",
                ),
                timeout=30,
            ),
            "splunk": MCPServerConfig(
                endpoint=os.getenv(
                    "SPLUNK_MCP_ENDPOINT",
                    "http://splunk-mcp-server.observability.svc.cluster.local:8080",
                ),
                timeout=30,
            ),
            "tempo": MCPServerConfig(
                endpoint=os.getenv(
                    "TEMPO_MCP_ENDPOINT",
                    "http://tempo-mcp-server.observability.svc.cluster.local:8080",
                ),
                timeout=15,
            ),
            "incidents-pg": MCPServerConfig(
                endpoint=os.getenv(
                    "INCIDENTS_PG_MCP_ENDPOINT",
                    "http://incidents-pg-mcp-server.observability.svc.cluster.local:8080",
                ),
                timeout=15,
            ),
            "athena": MCPServerConfig(
                endpoint=os.getenv(
                    "ATHENA_MCP_ENDPOINT",
                    "http://athena-mcp-server.observability.svc.cluster.local:8080",
                ),
                timeout=60,
            ),
        }

        self.standard_labels = [
            "application_service",
            "owner_squad",
            "severity",
            "env",
            "cluster",
            "namespace",
            "pod",
            "deployment",
            "trace_id",
        ]

        self.label_aliases = {
            "application_service": "application_service",
            "cmdb_ci_name": "application_service",
            "service.name": "application_service",
            "service": "application_service",
            "owner_squad": "owner_squad",
            "assignment_group_name": "owner_squad",
            "Severidade": "severity",
            "priority": "severity",
        }

        self.confidence_adjustments = {
            "multiple_signals_correlated": 1.2,
            "single_signal": 0.8,
            "has_trace_id": 1.1,
            "has_firing_alert": 1.1,
        }

        self.steering_context = self._load_steering()
        self.metrics_catalog = self._load_metrics_catalog()

    def _load_steering(self) -> Dict[str, str]:
        context = {}
        if STEERING_DIR.exists():
            for f in STEERING_DIR.glob("*.md"):
                context[f.stem] = f.read_text()
                log.info(f"Loaded steering file: {f.name}")
        return context

    def _load_metrics_catalog(self) -> list:
        """Carrega o catálogo de queries do steering file metrics-catalog.md.

        Parseia blocos YAML dentro do markdown e retorna lista de dicts
        com name, category, query_template, description.
        """
        import re
        import yaml

        catalog_file = STEERING_DIR / "metrics-catalog.md"
        if not catalog_file.exists():
            log.warning("metrics-catalog.md not found in steering dir")
            return []

        content = catalog_file.read_text()
        entries = []

        # Extrair blocos yaml do markdown
        yaml_blocks = re.findall(r"```yaml\s*\n(.*?)```", content, re.DOTALL)
        for block in yaml_blocks:
            try:
                parsed = yaml.safe_load(block)
                if isinstance(parsed, list):
                    entries.extend(parsed)
            except Exception as e:
                log.warning(f"Failed to parse YAML block in metrics-catalog.md: {e}")

        log.info(f"Loaded {len(entries)} queries from metrics-catalog.md")
        return entries


config = Config()
