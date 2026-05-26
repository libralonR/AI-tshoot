"""Config carregado de env vars + steering files + metrics catalog.

Mesmas env vars e arquivos que a versão atual. Steering e prompts moram em
`infrastructure/steering/` e `infrastructure/prompts/` para refletir que são
recursos de I/O.
"""

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import yaml

log = logging.getLogger("orchestrator")

# Diretórios baseados na localização desta pasta `infrastructure/`
_INFRA_DIR = Path(__file__).parent
STEERING_DIR = _INFRA_DIR / "steering"
PROMPTS_DIR = _INFRA_DIR / "prompts"
SPECS_DIR = _INFRA_DIR / "specs"


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

        self.steering_context = self._load_steering()
        self.metrics_catalog = self._load_metrics_catalog()

    def _load_steering(self) -> Dict[str, str]:
        context: Dict[str, str] = {}
        if STEERING_DIR.exists():
            for f in STEERING_DIR.glob("*.md"):
                context[f.stem] = f.read_text()
                log.info(f"Loaded steering file: {f.name}")
        return context

    def _load_metrics_catalog(self) -> List[Dict[str, str]]:
        catalog_file = STEERING_DIR / "metrics-catalog.md"
        if not catalog_file.exists():
            log.warning(
                f"[Config] metrics-catalog.md NOT FOUND in {STEERING_DIR} — "
                f"MetricsAdapter will NOT execute catalog queries during /investigate"
            )
            return []

        content = catalog_file.read_text()
        entries: List[Dict[str, str]] = []

        yaml_blocks = re.findall(r"```yaml\s*\n(.*?)```", content, re.DOTALL)
        log.info(f"[Config] Found {len(yaml_blocks)} YAML blocks in metrics-catalog.md")

        for idx, block in enumerate(yaml_blocks, 1):
            try:
                parsed = yaml.safe_load(block)
                if isinstance(parsed, list):
                    entries.extend(parsed)
                    log.info(f"[Config] YAML block {idx}: parsed {len(parsed)} entries")
                else:
                    log.warning(
                        f"[Config] YAML block {idx}: not a list, got {type(parsed).__name__}"
                    )
            except Exception as e:  # noqa: BLE001
                log.warning(f"[Config] YAML block {idx}: parse FAILED — {e}")

        valid: List[Dict[str, str]] = []
        for entry in entries:
            if "query_template" in entry and "name" in entry:
                valid.append(entry)
            else:
                log.warning(f"[Config] Invalid catalog entry: {entry}")

        log.info(
            f"[Config] Loaded metrics catalog | total_entries={len(valid)} | "
            f"categories={list(set(e.get('category', '?') for e in valid))}"
        )
        return valid


config = Config()
