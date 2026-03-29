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
        sslmode = os.getenv("PG_SSLMODE", "require")  # AWS RDS exige SSL
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
    cols = ", ".join(f'i."{c}"' for c in INCIDENT_COLUMNS)

    async with pool.connection() as conn:
        row = await conn.execute(
            f"SELECT {cols} FROM public.incidents_snow i WHERE i.number = %(number)s",
            {"number": number},
        )
        result = await row.fetchone()

    if not result:
        return {"success": False, "error": f"Incident {number} not found"}

    return {"success": True, "result": enrich_row(result)}


async def _search_incidents(pool: AsyncConnectionPool, args: dict) -> dict:
    conditions = []
    params: Dict[str, Any] = {}

    if args.get("application_service"):
        conditions.append("i.cmdb_ci_name ILIKE %(app_svc)s")
        params["app_svc"] = f"%{args['application_service']}%"

    if args.get("priority"):
        conditions.append("i.priority = %(priority)s")
        params["priority"] = args["priority"]

    if args.get("state"):
        conditions.append("i.state ILIKE %(state)s")
        params["state"] = f"%{args['state']}%"

    if args.get("category"):
        conditions.append("i.category ILIKE %(category)s")
        params["category"] = f"%{args['category']}%"

    if args.get("assignment_group_name"):
        conditions.append("i.assignment_group_name ILIKE %(agroup)s")
        params["agroup"] = f"%{args['assignment_group_name']}%"

    if args.get("opened_after"):
        conditions.append("i.opened_at >= %(opened_after)s::timestamptz")
        params["opened_after"] = args["opened_after"]

    if args.get("opened_before"):
        conditions.append("i.opened_at <= %(opened_before)s::timestamptz")
        params["opened_before"] = args["opened_before"]

    limit = min(int(args.get("limit", 50)), 200)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    cols = ", ".join(f'i."{c}"' for c in INCIDENT_COLUMNS)

    query = f"""
        SELECT {cols}
        FROM public.incidents_snow i
        {where}
        ORDER BY i.opened_at DESC
        LIMIT {limit}
    """

    async with pool.connection() as conn:
        cur = await conn.execute(query, params)
        rows = await cur.fetchall()

    return {
        "success": True,
        "result": [enrich_row(r) for r in rows],
        "count": len(rows),
    }


async def _get_related_incidents(pool: AsyncConnectionPool, args: dict) -> dict:
    time_window = int(args.get("time_window_hours", 24))
    cols = ", ".join(f'i."{c}"' for c in INCIDENT_COLUMNS)
    results = {"by_parent": [], "by_ci": []}

    async with pool.connection() as conn:
        if args.get("number"):
            number = args["number"].strip().upper()
            cur = await conn.execute(
                "SELECT cmdb_ci_name, opened_at, sys_id FROM public.incidents_snow WHERE number = %(number)s",
                {"number": number},
            )
            ref = await cur.fetchone()
            if not ref:
                return {"success": False, "error": f"Reference incident {number} not found"}

            ci_name = ref["cmdb_ci_name"]
            opened_at = ref["opened_at"]
            sys_id = ref["sys_id"]

            # Incidentes filhos ou com mesmo parent
            cur = await conn.execute(
                f"""SELECT {cols} FROM public.incidents_snow i
                    WHERE (i.parent_incident = %(sys_id)s OR i.parent_incident = %(number)s)
                    AND i.number != %(number)s
                    ORDER BY i.opened_at DESC LIMIT 50""",
                {"sys_id": sys_id, "number": number},
            )
            results["by_parent"] = [enrich_row(r) for r in await cur.fetchall()]

            # Mesmo CI na janela de tempo
            if ci_name:
                cur = await conn.execute(
                    f"""SELECT {cols} FROM public.incidents_snow i
                        WHERE i.cmdb_ci_name = %(ci_name)s
                        AND i.opened_at BETWEEN %(opened_at)s - interval '{time_window} hours'
                                              AND %(opened_at)s + interval '{time_window} hours'
                        AND i.number != %(number)s
                        ORDER BY i.opened_at DESC LIMIT 50""",
                    {"ci_name": ci_name, "opened_at": opened_at, "number": number},
                )
                results["by_ci"] = [enrich_row(r) for r in await cur.fetchall()]

        elif args.get("application_service"):
            cur = await conn.execute(
                f"""SELECT {cols} FROM public.incidents_snow i
                    WHERE i.cmdb_ci_name ILIKE %(app_svc)s
                    ORDER BY i.opened_at DESC LIMIT 50""",
                {"app_svc": f"%{args['application_service']}%"},
            )
            results["by_ci"] = [enrich_row(r) for r in await cur.fetchall()]

    return {
        "success": True,
        "result": results,
        "count": len(results["by_parent"]) + len(results["by_ci"]),
    }


async def _get_incident_stats(pool: AsyncConnectionPool, args: dict) -> dict:
    days = int(args.get("days", 30))
    group_by = args.get("group_by", "priority")

    if group_by not in ("priority", "category", "state", "assignment_group_name"):
        return {"success": False, "error": f"Invalid group_by: {group_by}"}

    conditions = [f"i.opened_at >= NOW() - interval '{days} days'"]
    params: Dict[str, Any] = {}

    if args.get("application_service"):
        conditions.append("i.cmdb_ci_name ILIKE %(app_svc)s")
        params["app_svc"] = f"%{args['application_service']}%"

    where = f"WHERE {' AND '.join(conditions)}"

    query = f"""
        SELECT i.{group_by} as group_key, COUNT(*) as count
        FROM public.incidents_snow i
        {where}
        GROUP BY i.{group_by}
        ORDER BY count DESC
    """

    async with pool.connection() as conn:
        cur = await conn.execute(query, params)
        rows = await cur.fetchall()

    total = sum(r["count"] for r in rows)
    return {
        "success": True,
        "result": [{"key": r["group_key"], "count": r["count"]} for r in rows],
        "total": total,
        "period_days": days,
        "group_by": group_by,
    }


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
