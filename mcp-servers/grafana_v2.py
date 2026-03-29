# mcp-servers/grafana_v2.py
# Grafana MCP Server (High-Level API) para Observability Troubleshooting Copilot

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server

# ------------------------
# Logging
# ------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger("grafana-mcp")

# ------------------------
# Config
# ------------------------
def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class GrafanaConfig:
    base_url: str
    token: str
    org_id: Optional[str]
    verify_tls: bool
    timeout_s: float

    @staticmethod
    def from_env() -> "GrafanaConfig":
        base_url = (os.getenv("GRAFANA_URL") or "").strip().rstrip("/")
        token = (os.getenv("GRAFANA_TOKEN") or "").strip()
        if not base_url or not token:
            raise RuntimeError(
                "Missing GRAFANA_URL or GRAFANA_TOKEN env vars"
            )
        return GrafanaConfig(
            base_url=base_url,
            token=token,
            org_id=(os.getenv("GRAFANA_ORG_ID") or "").strip() or None,
            verify_tls=not _env_bool("GRAFANA_INSECURE_SKIP_VERIFY", default=False),
            timeout_s=float(os.getenv("GRAFANA_TIMEOUT_S") or "15"),
        )


class GrafanaClient:
    def __init__(self, cfg: GrafanaConfig):
        headers = {"Authorization": f"Bearer {cfg.token}"}
        if cfg.org_id:
            headers["X-Grafana-Org-Id"] = cfg.org_id

        self._cfg = cfg
        self._client = httpx.AsyncClient(
            base_url=cfg.base_url,
            headers=headers,
            verify=cfg.verify_tls,
            timeout=httpx.Timeout(cfg.timeout_s),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        r = await self._client.get(path, params=params)
        r.raise_for_status()
        return r.json()

    async def get_alert_details(self, alert_uid: str) -> Dict[str, Any]:
        return await self.get(f"/api/v1/provisioning/alert-rules/{alert_uid}")

    async def find_firing_alerts(
        self, 
        labels: Optional[Dict[str, str]] = None, 
        dashboard_uid: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        params = {"active": "true", "silenced": "false", "inhibited": "false"}
        alerts = await self.get("/api/alertmanager/grafana/api/v2/alerts", params=params)
        
        if labels:
            filtered = []
            for alert in alerts:
                alert_labels = alert.get("labels", {})
                if all(alert_labels.get(k) == v for k, v in labels.items()):
                    filtered.append(alert)
            alerts = filtered
        
        if dashboard_uid:
            try:
                dashboard = await self.get_dashboard(dashboard_uid)
                dashboard_title = dashboard.get("dashboard", {}).get("title", "")
                filtered = []
                for alert in alerts:
                    annotations = alert.get("annotations", {})
                    if dashboard_uid in str(annotations) or dashboard_title in str(annotations):
                        filtered.append(alert)
                alerts = filtered
            except Exception as e:
                log.warning(f"Could not filter by dashboard {dashboard_uid}: {e}")
        
        return alerts

    async def find_dashboards(
        self, 
        labels: Optional[Dict[str, str]] = None, 
        tags: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        query_parts = []
        if labels:
            for key, value in labels.items():
                query_parts.append(f"{key}:{value}")
        
        query = " ".join(query_parts) if query_parts else ""
        params: Dict[str, Any] = {"type": "dash-db", "limit": 100}
        if query:
            params["query"] = query
        if tags:
            params["tag"] = tags
        
        dashboards = await self.get("/api/search", params=params)
        
        for dash in dashboards:
            dash["url"] = f"{self._cfg.base_url}{dash.get('url', '')}"
        
        return dashboards

    async def get_dashboard(self, uid: str) -> Dict[str, Any]:
        return await self.get(f"/api/dashboards/uid/{uid}")


def _slugify(title: str) -> str:
    s = title.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "dashboard"


def _build_dashboard_url(base_url: str, uid: str, title_or_slug: str) -> str:
    slug = _slugify(title_or_slug)
    return f"{base_url}/d/{uid}/{slug}"


def _build_time_params(time_from_ms: Optional[int], time_to_ms: Optional[int]) -> str:
    qp = {}
    if time_from_ms is not None:
        qp["from"] = str(int(time_from_ms))
    if time_to_ms is not None:
        qp["to"] = str(int(time_to_ms))
    return urlencode(qp)


# ------------------------
# MCP Server (High-Level API)
# ------------------------
app = Server("grafana-mcp")


@app.list_tools()
async def list_tools() -> list[dict]:
    return [
        {
            "name": "get_alert_details",
            "description": "Get Grafana alert details by UID",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "alertUID": {"type": "string", "description": "Grafana alert UID"}
                },
                "required": ["alertUID"],
            },
        },
        {
            "name": "find_firing_alerts",
            "description": "Find currently firing alerts. Supports filtering by labels including application_service, owner_squad, Severidade, business_service, etc.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "labels": {
                        "type": "object",
                        "description": "Filter by alert labels (ex: application_service, owner_squad, Severidade, alertname, grafana_folder)",
                        "additionalProperties": {"type": "string"}
                    },
                    "dashboardUID": {
                        "type": "string",
                        "description": "Filter by dashboard UID"
                    },
                    "severidade": {
                        "type": "string",
                        "description": "Filter by severity (P1, P2, P3)"
                    },
                    "application_service": {
                        "type": "string",
                        "description": "Filter by application_service (correlates with cmdb_ci_name in incidents)"
                    },
                    "owner_squad": {
                        "type": "string",
                        "description": "Filter by owner squad"
                    }
                },
            },
        },
        {
            "name": "find_dashboards",
            "description": "Find dashboards by labels and tags",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "labels": {
                        "type": "object",
                        "additionalProperties": {"type": "string"}
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
            },
        },
        {
            "name": "get_panel_link",
            "description": "Generate panel link with time range",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "dashboardUID": {"type": "string"},
                    "panelId": {"type": "integer"},
                    "timeRange": {
                        "type": "object",
                        "properties": {
                            "start": {"type": "integer"},
                            "end": {"type": "integer"}
                        }
                    }
                },
                "required": ["dashboardUID", "panelId"],
            },
        },
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[dict]:
    log.info(f"Tool called: {name} with arguments: {arguments}")
    
    cfg = GrafanaConfig.from_env()
    client = GrafanaClient(cfg)
    start_time = time.time()

    try:
        if name == "get_alert_details":
            alert_uid = str(arguments["alertUID"])
            alert_details = await client.get_alert_details(alert_uid)
            execution_time = time.time() - start_time
            
            alert_url = f"{cfg.base_url}/alerting/grafana/{alert_uid}/view"
            
            result = {
                "success": True,
                "result": {
                    "uid": alert_details.get("uid"),
                    "title": alert_details.get("title"),
                    "folderUID": alert_details.get("folderUID"),
                    "ruleGroup": alert_details.get("ruleGroup"),
                    "condition": alert_details.get("condition"),
                    "data": alert_details.get("data"),
                    "labels": alert_details.get("labels", {}),
                    "annotations": alert_details.get("annotations", {}),
                    "state": alert_details.get("state"),
                    "orgID": alert_details.get("orgID"),
                },
                "alertURL": alert_url,
                "executionTime": execution_time
            }
            
            return [{"type": "text", "text": json.dumps(result, indent=2)}]

        elif name == "find_firing_alerts":
            labels = dict(arguments.get("labels") or {})
            dashboard_uid = arguments.get("dashboardUID")
            
            # Merge convenience filters into labels
            if arguments.get("severidade"):
                labels["Severidade"] = arguments["severidade"]
            if arguments.get("application_service"):
                labels["application_service"] = arguments["application_service"]
            if arguments.get("owner_squad"):
                labels["owner_squad"] = arguments["owner_squad"]
            
            alerts = await client.find_firing_alerts(
                labels=labels if labels else None,
                dashboard_uid=dashboard_uid,
            )
            execution_time = time.time() - start_time
            
            normalized_alerts = []
            for alert in alerts:
                alert_labels = alert.get("labels", {})
                alert_annotations = alert.get("annotations", {})
                normalized = {
                    "fingerprint": alert.get("fingerprint"),
                    "status": alert.get("status", {}),
                    "labels": alert_labels,
                    "annotations": alert_annotations,
                    "startsAt": alert.get("startsAt"),
                    "endsAt": alert.get("endsAt"),
                    "generatorURL": alert.get("generatorURL"),
                    # Metadados de correlação extraídos dos labels
                    "correlation": {
                        "application_service": alert_labels.get("application_service"),
                        "business_service": alert_labels.get("business_service"),
                        "business_domain": alert_labels.get("business_domain"),
                        "business_capability": alert_labels.get("business_capability"),
                        "owner_squad": alert_labels.get("owner_squad"),
                        "owner_sre": alert_labels.get("owner_sre"),
                        "severidade": alert_labels.get("Severidade"),
                        "alertname": alert_labels.get("alertname"),
                        "grafana_folder": alert_labels.get("grafana_folder"),
                        "datasource": alert_labels.get("Datasource"),
                        "ops24by7": alert_labels.get("Ops24by7"),
                        "gic": alert_labels.get("GIC"),
                    },
                    # URLs do dashboard
                    "dashboard": {
                        "origin": alert_annotations.get("Origin"),
                        "panel_url": alert_annotations.get("Panel URL"),
                        "silence_url": alert_annotations.get("Silence URL"),
                    },
                }
                normalized_alerts.append(normalized)
            
            result = {
                "success": True,
                "result": normalized_alerts,
                "executionTime": execution_time
            }
            
            return [{"type": "text", "text": json.dumps(result, indent=2)}]

        elif name == "find_dashboards":
            labels = arguments.get("labels")
            tags = arguments.get("tags")
            dashboards = await client.find_dashboards(labels=labels, tags=tags)
            execution_time = time.time() - start_time
            
            normalized_dashboards = []
            for dash in dashboards:
                normalized = {
                    "title": dash.get("title"),
                    "uid": dash.get("uid"),
                    "type": dash.get("type"),
                    "folderTitle": dash.get("folderTitle"),
                    "folderUid": dash.get("folderUid"),
                    "tags": dash.get("tags", []),
                    "url": dash.get("url"),
                }
                normalized_dashboards.append(normalized)
            
            result = {
                "success": True,
                "result": normalized_dashboards,
                "executionTime": execution_time
            }
            
            return [{"type": "text", "text": json.dumps(result, indent=2)}]

        elif name == "get_panel_link":
            dashboard_uid = str(arguments["dashboardUID"])
            panel_id = int(arguments["panelId"])
            time_range = arguments.get("timeRange", {})
            
            dashboard = await client.get_dashboard(dashboard_uid)
            execution_time = time.time() - start_time
            
            dashboard_title = dashboard.get("dashboard", {}).get("title", dashboard_uid)
            base_url = _build_dashboard_url(cfg.base_url, dashboard_uid, dashboard_title)
            
            time_from_ms = time_range.get("start") if time_range else None
            time_to_ms = time_range.get("end") if time_range else None
            
            qp = _build_time_params(time_from_ms, time_to_ms)
            panel_url = f"{base_url}?viewPanel={panel_id}"
            if qp:
                panel_url = f"{panel_url}&{qp}"
            
            result = {
                "success": True,
                "panelURL": panel_url,
                "executionTime": execution_time
            }
            
            return [{"type": "text", "text": json.dumps(result, indent=2)}]

        else:
            raise ValueError(f"Unknown tool: {name}")

    except httpx.HTTPStatusError as e:
        error_result = {
            "success": False,
            "error": f"HTTP error {e.response.status_code}: {e.response.text[:200]}",
            "executionTime": time.time() - start_time
        }
        return [{"type": "text", "text": json.dumps(error_result, indent=2)}]
        
    except Exception as e:
        log.exception(f"Error in tool {name}")
        error_result = {
            "success": False,
            "error": str(e),
            "executionTime": time.time() - start_time
        }
        return [{"type": "text", "text": json.dumps(error_result, indent=2)}]
        
    finally:
        await client.close()


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
