#!/usr/bin/env python3
"""
Incidents PostgreSQL MCP Server
Busca incidentes do ServiceNow armazenados em PostgreSQL (AWS RDS).
Read-only por design (guardrail).
Usa psycopg 3 (async) para conexão.
"""

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs, unquote

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from mcp.server import Server
from mcp.server.stdio import stdio_server

# Logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("incidents-pg-mcp")


# Config
@dataclass(frozen=True)
class PGConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    sslmode: str
    min_connections: int
    max_connections: int

    @staticmethod
    def from_env() -> "PGConfig":
        host = os.getenv("PG_HOST", "localhost")
        port = int(os.getenv("PG_PORT", "5432"))
        database = os.getenv("PG_DATABASE", "incidents")
        user = os.getenv("PG_USER", "")
        password = os.getenv("PG_PASSWORD", "")
        sslmode = os.getenv("PG_SSLMODE", "require")
        log.info(f"Config: PG_HOST={host}, PG_PORT={port}, PG_DATABASE={database}, "
                 f"PG_USER={'set' if user else 'MISSING'}, PG_SSLMODE={sslmode}")
        if not user or not password:
            raise RuntimeError("Missing PG_USER or PG_PASSWORD env vars")
        return PGConfig(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            sslmode=sslmode,
            min_connections=int(os.getenv("PG_MIN_CONN", "1")),
            max_connections=int(os.getenv("PG_MAX_CONN", "5")),
        )

    @property
    def conninfo(self) -> str:
        return (
            f"host={self.host} port={self.port} dbname={self.database} "
            f"user={self.user} password={self.password} sslmode={self.sslmode}"
        )


# Colunas disponíveis na tabela incidents_snow
INCIDENT_COLUMNS = [
    "sys_id", "number", "short_description", "opened_at",
    "sys_created_by", "impact", "description", "category",
    "subcategory", "urgency", "location", "cmdb_ci",
    "assignment_group", "state", "priority",
    "assignment_group_name", "cmdb_ci_name", "location_name",
    "parent_incident",
]

# Pool global
_pool: Optional[AsyncConnectionPool] = None


async def get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        cfg = PGConfig.from_env()
        _pool = AsyncConnectionPool(
            conninfo=cfg.conninfo,
            min_size=cfg.min_connections,
            max_size=cfg.max_connections,
            kwargs={"row_factory": dict_row},
        )
        await _pool.open()
        log.info(f"Connected to PostgreSQL {cfg.host}:{cfg.port}/{cfg.database} (sslmode={cfg.sslmode})")
    return _pool


def serialize_row(row: dict) -> Dict[str, Any]:
    """Converte valores não-serializáveis (datetime, etc.) para string"""
    d = dict(row)
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d


def parse_description(description: str) -> Dict[str, Any]:
    """Extrai metadados estruturados do campo description do incidente.

    O description contém o corpo do alerta Grafana com:
    - Texto livre no início
    - URLs: Origin, Panel URL, Silence URL
    - Bloco Labels: com formato '- key=value' por linha
    - O alert_rule_uid está no Silence URL (matcher __alert_rule_uid__)
    """
    if not description:
        return {}

    parsed: Dict[str, Any] = {}

    # Extrair URLs
    origin_match = re.search(r"Origin:\s*(https?://\S+)", description)
    if origin_match:
        parsed["origin_url"] = origin_match.group(1)

    panel_match = re.search(r"Panel URL:\s*(https?://\S+)", description)
    if panel_match:
        parsed["panel_url"] = panel_match.group(1)

    silence_match = re.search(r"Silence URL:\s*(https?://\S+)", description)
    if silence_match:
        silence_url = silence_match.group(1)
        parsed["silence_url"] = silence_url

        # Extrair alert_rule_uid do Silence URL
        try:
            qs = parse_qs(urlparse(silence_url).query)
            for matcher in qs.get("matcher", []):
                decoded = unquote(matcher)
                if "__alert_rule_uid__" in decoded:
                    # formato: __alert_rule_uid__%3Dvalue ou __alert_rule_uid__=value
                    uid = decoded.split("=", 1)[-1] if "=" in decoded else None
                    if uid:
                        parsed["alert_rule_uid"] = uid
        except Exception:
            pass

    # Extrair bloco Labels (formato: - key=value)
    labels: Dict[str, str] = {}
    labels_section = re.search(r"Labels:\s*\n((?:\s*-\s*.+=.+\n?)+)", description)
    if labels_section:
        for line in labels_section.group(1).strip().split("\n"):
            line = line.strip().lstrip("- ").strip()
            if "=" in line:
                key, _, value = line.partition("=")
                labels[key.strip()] = value.strip()

    if labels:
        parsed["grafana_labels"] = labels

    return parsed


def enrich_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Enriquece o resultado do incidente com labels extraídas do description."""
    enriched = serialize_row(row)
    description = enriched.get("description", "")
    if description:
        parsed = parse_description(description)
        if parsed:
            enriched["_parsed"] = parsed
            # Promover labels do Grafana para o nível raiz para facilitar correlação
            gl = parsed.get("grafana_labels", {})
            if gl:
                enriched["_grafana_labels"] = gl
    return enriched


# MCP Server
app = Server("incidents-pg-mcp")


@app.list_tools()
async def list_tools() -> list[dict]:
    return [
        {
            "name": "get_incident",
            "description": "Busca um incidente pelo número (ex: INC0012345)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "number": {
                        "type": "string",
                        "description": "Número do incidente (ex: INC0012345)",
                    }
                },
                "required": ["number"],
            },
        },
        {
            "name": "search_incidents",
            "description": "Busca incidentes por filtros (application_service/cmdb_ci, priority, state, category, assignment_group, date range)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "application_service": {
                        "type": "string",
                        "description": "Nome do serviço/componente (busca em cmdb_ci_name)",
                    },
                    "priority": {
                        "type": "string",
                        "description": "Prioridade (1-Critical, 2-High, 3-Moderate, 4-Low)",
                    },
                    "state": {
                        "type": "string",
                        "description": "Estado do incidente (New, In Progress, Resolved, Closed)",
                    },
                    "category": {
                        "type": "string",
                        "description": "Categoria do incidente",
                    },
                    "assignment_group_name": {
                        "type": "string",
                        "description": "Nome do grupo de atribuição (owner_squad)",
                    },
                    "opened_after": {
                        "type": "string",
                        "description": "Incidentes abertos após esta data (ISO 8601)",
                    },
                    "opened_before": {
                        "type": "string",
                        "description": "Incidentes abertos antes desta data (ISO 8601)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Máximo de resultados (default: 50, max: 200)",
                    },
                },
            },
        },
        {
            "name": "get_related_incidents",
            "description": "Busca incidentes relacionados (mesmo application_service ou parent_incident)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "number": {
                        "type": "string",
                        "description": "Número do incidente de referência",
                    },
                    "application_service": {
                        "type": "string",
                        "description": "Nome do serviço para buscar incidentes relacionados",
                    },
                    "time_window_hours": {
                        "type": "integer",
                        "description": "Janela de tempo em horas (default: 24)",
                    },
                },
            },
        },
        {
            "name": "get_incident_stats",
            "description": "Estatísticas de incidentes por serviço, prioridade ou período",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "application_service": {
                        "type": "string",
                        "description": "Filtrar por serviço",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Período em dias (default: 30)",
                    },
                    "group_by": {
                        "type": "string",
                        "description": "Agrupar por: priority, category, state, assignment_group_name",
                        "enum": ["priority", "category", "state", "assignment_group_name"],
                    },
                },
            },
        },
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[dict]:
    log.info(f"Tool called: {name} with arguments: {arguments}")
    pool = await get_pool()
    start_time = time.time()

    try:
        if name == "get_incident":
            result = await _get_incident(pool, arguments)
        elif name == "search_incidents":
            result = await _search_incidents(pool, arguments)
        elif name == "get_related_incidents":
            result = await _get_related_incidents(pool, arguments)
        elif name == "get_incident_stats":
            result = await _get_incident_stats(pool, arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")

        result["executionTime"] = time.time() - start_time
        return [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]

    except Exception as e:
        log.exception(f"Error in tool {name}")
        error_result = {
            "success": False,
            "error": str(e),
            "executionTime": time.time() - start_time,
        }
        return [{"type": "text", "text": json.dumps(error_result, indent=2)}]


async def _get_incident(pool: AsyncConnectionPool, args: dict) -> dict:
    number = args["number"].strip().upper()
    log.info(f"[get_incident] Starting fetch for incident: {number}")
    cols = ", ".join(f'i."{c}"' for c in INCIDENT_COLUMNS)

    try:
        async with pool.connection() as conn:
            log.debug(f"[get_incident] Executing query for {number}")
            row = await conn.execute(
                f"SELECT {cols} FROM public.incidents_snow i WHERE i.number = %(number)s",
                {"number": number},
            )
            result = await row.fetchone()
            log.debug(f"[get_incident] Query completed, result={'found' if result else 'not found'}")

        if not result:
            log.warning(f"[get_incident] Incident {number} not found in database")
            return {"success": False, "error": f"Incident {number} not found"}

        enriched = enrich_row(result)
        labels_count = len(enriched.get("_grafana_labels", {}))
        parsed_data = enriched.get("_parsed", {})
        
        log.info(
            f"[get_incident] Successfully fetched {number} | "
            f"cmdb_ci_name={enriched.get('cmdb_ci_name')} | "
            f"priority={enriched.get('priority')} | "
            f"state={enriched.get('state')} | "
            f"assignment_group={enriched.get('assignment_group_name')} | "
            f"grafana_labels_count={labels_count} | "
            f"alert_rule_uid={parsed_data.get('alert_rule_uid', 'N/A')}"
        )
        
        return {"success": True, "result": enriched}
    
    except Exception as e:
        log.error(f"[get_incident] Error fetching {number}: {type(e).__name__}: {e}")
        raise


async def _search_incidents(pool: AsyncConnectionPool, args: dict) -> dict:
    conditions = []
    params: Dict[str, Any] = {}
    
    log.info(f"[search_incidents] Starting search with filters: {json.dumps(args, default=str)}")

    # PRIORIDADE: Buscar application_service no description (onde sempre está)
    if args.get("application_service"):
        app_svc = args['application_service']
        # Buscar tanto no cmdb_ci_name quanto no description
        conditions.append(
            "(i.cmdb_ci_name ILIKE %(app_svc)s OR "
            "i.description ILIKE %(app_svc_label)s OR "
            "i.description ILIKE %(instance_label)s OR "
            "i.description ILIKE %(ci_label)s)"
        )
        params["app_svc"] = f"%{app_svc}%"
        params["app_svc_label"] = f"%application_service={app_svc}%"
        params["instance_label"] = f"%instance={app_svc}%"
        params["ci_label"] = f"%CI:{app_svc}%"
        log.debug(f"[search_incidents] Filter: application_service in cmdb_ci_name OR description")

    if args.get("priority"):
        conditions.append("i.priority = %(priority)s")
        params["priority"] = args["priority"]
        log.debug(f"[search_incidents] Filter: priority = {args['priority']}")

    if args.get("state"):
        conditions.append("i.state ILIKE %(state)s")
        params["state"] = f"%{args['state']}%"
        log.debug(f"[search_incidents] Filter: state LIKE '%{args['state']}%'")

    if args.get("category"):
        conditions.append("i.category ILIKE %(category)s")
        params["category"] = f"%{args['category']}%"
        log.debug(f"[search_incidents] Filter: category LIKE '%{args['category']}%'")

    if args.get("assignment_group_name"):
        conditions.append("i.assignment_group_name ILIKE %(agroup)s")
        params["agroup"] = f"%{args['assignment_group_name']}%"
        log.debug(f"[search_incidents] Filter: assignment_group_name LIKE '%{args['assignment_group_name']}%'")

    if args.get("opened_after"):
        conditions.append("i.opened_at >= %(opened_after)s::timestamptz")
        params["opened_after"] = args["opened_after"]
        log.debug(f"[search_incidents] Filter: opened_at >= {args['opened_after']}")

    if args.get("opened_before"):
        conditions.append("i.opened_at <= %(opened_before)s::timestamptz")
        params["opened_before"] = args["opened_before"]
        log.debug(f"[search_incidents] Filter: opened_at <= {args['opened_before']}")

    limit = min(int(args.get("limit", 50)), 200)
    log.debug(f"[search_incidents] Limit: {limit} (requested: {args.get('limit', 50)})")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    cols = ", ".join(f'i."{c}"' for c in INCIDENT_COLUMNS)

    query = f"""
        SELECT {cols}
        FROM public.incidents_snow i
        {where}
        ORDER BY i.opened_at DESC
        LIMIT {limit}
    """

    try:
        async with pool.connection() as conn:
            log.debug(f"[search_incidents] Executing query with {len(conditions)} conditions")
            cur = await conn.execute(query, params)
            rows = await cur.fetchall()
            log.debug(f"[search_incidents] Query returned {len(rows)} rows")

        enriched_results = [enrich_row(r) for r in rows]
        
        # Log summary of results
        if enriched_results:
            services = set(r.get('cmdb_ci_name') for r in enriched_results if r.get('cmdb_ci_name'))
            priorities = {}
            for r in enriched_results:
                p = r.get('priority', 'unknown')
                priorities[p] = priorities.get(p, 0) + 1
            
            log.info(
                f"[search_incidents] Successfully returned {len(enriched_results)} incidents | "
                f"unique_services={len(services)} | "
                f"priority_distribution={priorities}"
            )
        else:
            log.info(f"[search_incidents] No incidents found matching filters")
        
        return {
            "success": True,
            "result": enriched_results,
            "count": len(enriched_results),
        }
    
    except Exception as e:
        log.error(f"[search_incidents] Error executing query: {type(e).__name__}: {e}")
        raise


async def _get_related_incidents(pool: AsyncConnectionPool, args: dict) -> dict:
    time_window = int(args.get("time_window_hours", 24))
    number = args.get('number')
    app_svc = args.get('application_service')
    
    log.info(
        f"[get_related_incidents] Starting search | "
        f"number={number} | "
        f"application_service={app_svc} | "
        f"time_window={time_window}h"
    )
    
    cols = ", ".join(f'i."{c}"' for c in INCIDENT_COLUMNS)
    results = {"by_parent": [], "by_ci": [], "by_description": []}

    try:
        async with pool.connection() as conn:
            if number:
                number = number.strip().upper()
                log.debug(f"[get_related_incidents] Fetching reference incident: {number}")
                
                cur = await conn.execute(
                    "SELECT cmdb_ci_name, opened_at, sys_id, description FROM public.incidents_snow WHERE number = %(number)s",
                    {"number": number},
                )
                ref = await cur.fetchone()
                
                if not ref:
                    log.warning(f"[get_related_incidents] Reference incident {number} not found")
                    return {"success": False, "error": f"Reference incident {number} not found"}

                ci_name = ref["cmdb_ci_name"]
                opened_at = ref["opened_at"]
                sys_id = ref["sys_id"]
                description = ref["description"]
                
                # Extrair application_service do description do incidente de referência
                ref_app_svc = None
                if description:
                    parsed = parse_description(description)
                    grafana_labels = parsed.get("grafana_labels", {})
                    ref_app_svc = grafana_labels.get("application_service")
                
                log.debug(
                    f"[get_related_incidents] Reference incident found | "
                    f"cmdb_ci_name={ci_name} | "
                    f"application_service_from_description={ref_app_svc} | "
                    f"opened_at={opened_at} | "
                    f"sys_id={sys_id}"
                )

                # Incidentes filhos ou com mesmo parent
                log.debug(f"[get_related_incidents] Searching for child/sibling incidents")
                cur = await conn.execute(
                    f"""SELECT {cols} FROM public.incidents_snow i
                        WHERE (i.parent_incident = %(sys_id)s OR i.parent_incident = %(number)s)
                        AND i.number != %(number)s
                        ORDER BY i.opened_at DESC LIMIT 50""",
                    {"sys_id": sys_id, "number": number},
                )
                by_parent_rows = await cur.fetchall()
                results["by_parent"] = [enrich_row(r) for r in by_parent_rows]
                log.debug(f"[get_related_incidents] Found {len(by_parent_rows)} incidents by parent relationship")

                # PRIORIDADE: Buscar por application_service extraído do description
                if ref_app_svc:
                    log.debug(f"[get_related_incidents] Priority search by application_service from description: {ref_app_svc}")
                    cur = await conn.execute(
                        f"""SELECT {cols} FROM public.incidents_snow i
                            WHERE (
                                i.description ILIKE %(app_svc_label)s
                                OR i.description ILIKE %(instance_label)s
                                OR i.description ILIKE %(ci_label)s
                            )
                            AND i.opened_at BETWEEN %(opened_at)s - interval '{time_window} hours'
                                                  AND %(opened_at)s + interval '{time_window} hours'
                            AND i.number != %(number)s
                            ORDER BY i.opened_at DESC LIMIT 100""",
                        {
                            "app_svc_label": f"%application_service={ref_app_svc}%",
                            "instance_label": f"%instance={ref_app_svc}%",
                            "ci_label": f"%CI:{ref_app_svc}%",
                            "opened_at": opened_at,
                            "number": number,
                        },
                    )
                    by_desc_rows = await cur.fetchall()
                    results["by_description"] = [enrich_row(r) for r in by_desc_rows]
                    log.debug(f"[get_related_incidents] Found {len(by_desc_rows)} incidents by application_service in description")

                # FALLBACK: Buscar por cmdb_ci_name se existir e não encontrou nada ainda
                if ci_name and not results["by_description"]:
                    log.debug(f"[get_related_incidents] Fallback: Searching by cmdb_ci_name: {ci_name}")
                    cur = await conn.execute(
                        f"""SELECT {cols} FROM public.incidents_snow i
                            WHERE i.cmdb_ci_name = %(ci_name)s
                            AND i.opened_at BETWEEN %(opened_at)s - interval '{time_window} hours'
                                                  AND %(opened_at)s + interval '{time_window} hours'
                            AND i.number != %(number)s
                            ORDER BY i.opened_at DESC LIMIT 50""",
                        {"ci_name": ci_name, "opened_at": opened_at, "number": number},
                    )
                    by_ci_rows = await cur.fetchall()
                    results["by_ci"] = [enrich_row(r) for r in by_ci_rows]
                    log.debug(f"[get_related_incidents] Found {len(by_ci_rows)} incidents by cmdb_ci_name (fallback)")
                elif not ref_app_svc and not ci_name:
                    log.warning(f"[get_related_incidents] No application_service or cmdb_ci_name found for reference incident")

            elif app_svc:
                log.debug(f"[get_related_incidents] Searching by application_service: {app_svc}")
                
                # PRIORIDADE 1: Buscar no description (onde as informações SEMPRE estão)
                log.debug(f"[get_related_incidents] Priority search: description field for Grafana labels")
                cur = await conn.execute(
                    f"""SELECT {cols} FROM public.incidents_snow i
                        WHERE (
                            i.description ILIKE %(app_svc_label)s
                            OR i.description ILIKE %(instance_label)s
                            OR i.description ILIKE %(ci_label)s
                        )
                        AND i.opened_at >= NOW() - interval '{time_window} hours'
                        ORDER BY i.opened_at DESC LIMIT 100""",
                    {
                        "app_svc_label": f"%application_service={app_svc}%",
                        "instance_label": f"%instance={app_svc}%",
                        "ci_label": f"%CI:{app_svc}%",
                    },
                )
                by_desc_rows = await cur.fetchall()
                results["by_description"] = [enrich_row(r) for r in by_desc_rows]
                log.debug(f"[get_related_incidents] Found {len(by_desc_rows)} incidents by description (priority)")
                
                # FALLBACK: Buscar por cmdb_ci_name (pode estar vazio, então é fallback)
                log.debug(f"[get_related_incidents] Fallback search: cmdb_ci_name field")
                cur = await conn.execute(
                    f"""SELECT {cols} FROM public.incidents_snow i
                        WHERE i.cmdb_ci_name ILIKE %(app_svc)s
                        AND i.opened_at >= NOW() - interval '{time_window} hours'
                        ORDER BY i.opened_at DESC LIMIT 50""",
                    {"app_svc": f"%{app_svc}%"},
                )
                by_ci_rows = await cur.fetchall()
                
                # Remover duplicatas (incidentes que já estão em by_description)
                by_desc_numbers = {r.get('number') for r in results["by_description"]}
                unique_by_ci = [r for r in by_ci_rows if r.get('number') not in by_desc_numbers]
                
                results["by_ci"] = [enrich_row(r) for r in unique_by_ci]
                log.debug(
                    f"[get_related_incidents] Found {len(by_ci_rows)} incidents by cmdb_ci_name "
                    f"({len(unique_by_ci)} unique after dedup)"
                )
            else:
                log.warning(f"[get_related_incidents] No number or application_service provided")

        total_count = len(results["by_parent"]) + len(results["by_ci"]) + len(results["by_description"])
        log.info(
            f"[get_related_incidents] Search completed | "
            f"by_parent={len(results['by_parent'])} | "
            f"by_ci={len(results['by_ci'])} | "
            f"by_description={len(results['by_description'])} | "
            f"total={total_count}"
        )
        
        return {
            "success": True,
            "result": results,
            "count": total_count,
        }
    
    except Exception as e:
        log.error(f"[get_related_incidents] Error: {type(e).__name__}: {e}")
        raise


async def _get_incident_stats(pool: AsyncConnectionPool, args: dict) -> dict:
    days = int(args.get("days", 30))
    group_by = args.get("group_by", "priority")
    app_svc = args.get('application_service')
    
    log.info(
        f"[get_incident_stats] Starting stats calculation | "
        f"application_service={app_svc} | "
        f"days={days} | "
        f"group_by={group_by}"
    )

    if group_by not in ("priority", "category", "state", "assignment_group_name"):
        log.error(f"[get_incident_stats] Invalid group_by value: {group_by}")
        return {"success": False, "error": f"Invalid group_by: {group_by}"}

    conditions = [f"i.opened_at >= NOW() - interval '{days} days'"]
    params: Dict[str, Any] = {}

    if app_svc:
        conditions.append("i.cmdb_ci_name ILIKE %(app_svc)s")
        params["app_svc"] = f"%{app_svc}%"
        log.debug(f"[get_incident_stats] Filter: application_service LIKE '%{app_svc}%'")

    where = f"WHERE {' AND '.join(conditions)}"

    query = f"""
        SELECT i.{group_by} as group_key, COUNT(*) as count
        FROM public.incidents_snow i
        {where}
        GROUP BY i.{group_by}
        ORDER BY count DESC
    """

    try:
        async with pool.connection() as conn:
            log.debug(f"[get_incident_stats] Executing stats query")
            cur = await conn.execute(query, params)
            rows = await cur.fetchall()
            log.debug(f"[get_incident_stats] Query returned {len(rows)} groups")

        total = sum(r["count"] for r in rows)
        result_data = [{"key": r["group_key"], "count": r["count"]} for r in rows]
        
        log.info(
            f"[get_incident_stats] Stats calculated | "
            f"groups={len(rows)} | "
            f"total_incidents={total} | "
            f"period={days} days"
        )
        
        # Log top 3 groups
        for i, row in enumerate(result_data[:3], 1):
            log.debug(f"[get_incident_stats] Top {i}: {row['key']} = {row['count']} incidents")
        
        return {
            "success": True,
            "result": result_data,
            "total": total,
            "period_days": days,
            "group_by": group_by,
        }
    
    except Exception as e:
        log.error(f"[get_incident_stats] Error: {type(e).__name__}: {e}")
        raise


async def main_stdio():
    """Run in stdio mode (for Kiro local / CLI)."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


def main_sse():
    """Run in SSE mode with REST endpoints (for Docker / K8s)."""
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route, Mount
    from mcp.server.sse import SseServerTransport
    import uvicorn

    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await app.run(
                streams[0], streams[1], app.create_initialization_options()
            )

    async def handle_health(request):
        return JSONResponse({"status": "ok"})

    async def handle_tool_call(request: Request):
        tool_name = request.path_params["tool_name"]
        body = await request.json()
        arguments = body.get("arguments", {})
        log.info(f"REST /tools/{tool_name} called with: {arguments}")
        try:
            result = await call_tool(tool_name, arguments)
            text = result[0]["text"] if result else "{}"
            parsed = json.loads(text)
            log.info(f"REST /tools/{tool_name} success={parsed.get('success')}")
            return JSONResponse(parsed)
        except Exception as e:
            log.exception(f"REST /tools/{tool_name} error: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    async def handle_list_tools_endpoint(request):
        tools = await list_tools()
        return JSONResponse({"tools": tools})

    starlette_app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
            Route("/health", endpoint=handle_health),
            Route("/tools", endpoint=handle_list_tools_endpoint),
            Route("/tools/{tool_name}", endpoint=handle_tool_call, methods=["POST"]),
        ]
    )

    port = int(os.getenv("MCP_LISTEN_PORT", "8080"))
    log.info(f"Starting Incidents PG MCP Server in SSE mode on port {port}")
    uvicorn.run(starlette_app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    mode = os.getenv("MCP_SERVER_MODE", "stdio").lower()
    log.info(f"Starting incidents_pg.py in mode={mode}")
    log.info(f"ENV: PG_HOST={os.getenv('PG_HOST', 'NOT SET')}")
    log.info(f"ENV: PG_DATABASE={os.getenv('PG_DATABASE', 'NOT SET')}")
    log.info(f"ENV: MCP_SERVER_MODE={os.getenv('MCP_SERVER_MODE', 'NOT SET')}")
    if mode == "sse":
        main_sse()
    else:
        asyncio.run(main_stdio())
