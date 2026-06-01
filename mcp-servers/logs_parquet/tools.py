"""
Tool implementation functions for the Logs Parquet MCP Server.

This module owns the mutable global state (_config, _creds, _pool, _schema).
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .config import LogsConfig, DEFAULT_LIMIT, MAX_LIMIT, log
from .aws_credentials import AWSCredentialsManager
from .duckdb_pool import DuckDBPool, SchemaCache
from .partitions import (
    _parse_time,
    _build_globs_for_call,
    _column_quoted,
    _read_parquet_clause,
)

# ---------------------------------------------------------------------------
# Column constants
# ---------------------------------------------------------------------------

_BCAP_COL = _column_quoted("business-capability")
_APP_SVC_COL = _column_quoted("application-service")
_BSVC_COL = _column_quoted("business-service")
_BDOMAIN_COL = _column_quoted("business-domain")


# ---------------------------------------------------------------------------
# Globals (mutable state)
# ---------------------------------------------------------------------------

_config: Optional[LogsConfig] = None
_creds: Optional[AWSCredentialsManager] = None
_pool: Optional[DuckDBPool] = None
_schema: Optional[SchemaCache] = None


def _ensure_initialized() -> None:
    global _config, _creds, _pool, _schema
    if _config is None:
        _config = LogsConfig.from_env()
    if _creds is None:
        _creds = AWSCredentialsManager(_config)
    if _pool is None:
        _pool = DuckDBPool(_config, _creds)
    if _schema is None:
        _schema = SchemaCache(_pool)


def _globs_for_call(
    business_capability: Optional[str],
    start_dt: datetime,
    end_dt: datetime,
) -> List[str]:
    """Wrapper local que injeta config + credenciais no builder de globs.

    Mantém `_build_globs_for_call` em `partitions.py` puro (sem state global),
    e isola o acesso aos singletons `_config` e `_creds` aqui em tools.py.
    """
    aws_creds = _creds.get() if _creds else None
    aws_region = _config.aws_region if _config else None
    bucket = _config.bucket if _config else ""
    return _build_globs_for_call(
        business_capability=business_capability,
        start_dt=start_dt,
        end_dt=end_dt,
        bucket=bucket,
        aws_region=aws_region,
        aws_credentials=aws_creds,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce_limit(value: Any) -> int:
    try:
        n = int(value) if value is not None else DEFAULT_LIMIT
    except (ValueError, TypeError):
        n = DEFAULT_LIMIT
    return max(1, min(n, MAX_LIMIT))


def _common_filters(
    application_service: Optional[str],
    level: Optional[str],
    text_match: Optional[str],
    start_dt: datetime,
    end_dt: datetime,
    extra_clauses: Optional[List[str]] = None,
) -> tuple:
    """Constrói WHERE + parâmetros prepared. Inclui filtro temporal sempre."""
    clauses = [
        f"CAST({_column_quoted('time')} AS TIMESTAMPTZ) BETWEEN ? AND ?",
    ]
    params: list = [start_dt, end_dt]
    if application_service:
        clauses.append(f"{_APP_SVC_COL} = ?")
        params.append(application_service)
    if level:
        clauses.append(f"upper({_column_quoted('level')}) = ?")
        params.append(level.upper())
    if text_match:
        clauses.append(f"{_column_quoted('message')} ILIKE ?")
        params.append(f"%{text_match}%")
    if extra_clauses:
        clauses.extend(extra_clauses)
    return " AND ".join(clauses), params


def _serialize_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return str(v)
    return v


def _serialize_row(row) -> list:
    return [_serialize_value(v) for v in row]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

# ---------------- search_logs ----------------

def search_logs(
    application_service: Optional[str] = None,
    business_capability: Optional[str] = None,
    level: Optional[str] = None,
    text_match: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Busca livre por logs com filtros opcionais."""
    _ensure_initialized()
    start_t = time.time()

    if not start:
        raise ValueError("'start' is required (ISO 8601 or epoch_ms)")
    end_dt = _parse_time(end) if end else datetime.now(timezone.utc)
    start_dt = _parse_time(start)
    globs = _globs_for_call(business_capability, start_dt, end_dt)
    where_sql, params = _common_filters(
        application_service, level, text_match, start_dt, end_dt
    )
    n = _coerce_limit(limit)

    sql = (
        f"SELECT {_column_quoted('time')} AS time, "
        f"{_column_quoted('level')} AS level, "
        f"{_column_quoted('message')} AS message, "
        f"{_BCAP_COL} AS business_capability, "
        f"{_BDOMAIN_COL} AS business_domain, "
        f"{_BSVC_COL} AS business_service, "
        f"{_APP_SVC_COL} AS application_service, "
        f"{_column_quoted('args')} AS args, "
        f"{_column_quoted('extra-fields')} AS extra_fields "
        f"FROM {_read_parquet_clause(globs)} "
        f"WHERE {where_sql} "
        f"ORDER BY {_column_quoted('time')} DESC "
        f"LIMIT {n}"
    )

    log.info(
        f"[search_logs] capability={business_capability or '*'} | "
        f"service={application_service} | level={level} | "
        f"text_match={'set' if text_match else 'none'} | "
        f"window={start_dt.isoformat()}→{end_dt.isoformat()} | "
        f"globs={len(globs)} | limit={n}"
    )

    with _pool.acquire() as conn:
        rows = conn.execute(sql, params).fetchall()
        cols = [d[0] for d in conn.description]

    items = [dict(zip(cols, _serialize_row(row))) for row in rows]
    return {
        "success": True,
        "result": items,
        "count": len(items),
        "executionTime": time.time() - start_t,
    }


# ---------------- count_logs_by_level ----------------

def count_logs_by_level(
    application_service: Optional[str] = None,
    business_capability: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Dict[str, Any]:
    _ensure_initialized()
    start_t = time.time()
    if not start:
        raise ValueError("'start' is required")
    end_dt = _parse_time(end) if end else datetime.now(timezone.utc)
    start_dt = _parse_time(start)

    globs = _globs_for_call(business_capability, start_dt, end_dt)
    where_sql, params = _common_filters(
        application_service, None, None, start_dt, end_dt
    )

    sql = (
        f"SELECT upper({_column_quoted('level')}) AS level, count(*) AS count "
        f"FROM {_read_parquet_clause(globs)} "
        f"WHERE {where_sql} "
        f"GROUP BY 1 ORDER BY 2 DESC"
    )
    log.info(f"[count_logs_by_level] capability={business_capability or '*'} | service={application_service}")

    with _pool.acquire() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {
        "success": True,
        "result": [{"level": r[0], "count": int(r[1])} for r in rows],
        "executionTime": time.time() - start_t,
    }


# ---------------- find_error_patterns ----------------

def find_error_patterns(
    application_service: str,
    start: str,
    end: Optional[str] = None,
    business_capability: Optional[str] = None,
    top_n: Optional[int] = 10,
) -> Dict[str, Any]:
    _ensure_initialized()
    start_t = time.time()
    if not application_service:
        raise ValueError("'application_service' is required for find_error_patterns")
    end_dt = _parse_time(end) if end else datetime.now(timezone.utc)
    start_dt = _parse_time(start)

    globs = _globs_for_call(business_capability, start_dt, end_dt)
    where_sql, params = _common_filters(
        application_service, "ERROR", None, start_dt, end_dt
    )
    n = _coerce_limit(top_n)

    # Normalização: substitui números, UUIDs e quoted strings por placeholders
    # para agrupar mensagens equivalentes.
    pattern_expr = (
        f"regexp_replace("
        f"  regexp_replace("
        f"    regexp_replace({_column_quoted('message')}, '[a-f0-9]{{8}}-[a-f0-9]{{4}}-[a-f0-9]{{4}}-[a-f0-9]{{4}}-[a-f0-9]{{12}}', '<UUID>', 'g'),"
        f"  '[0-9]+', '<N>', 'g'),"
        f" '\"[^\"]*\"', '<STR>', 'g')"
    )

    sql = (
        f"SELECT {pattern_expr} AS pattern, count(*) AS occurrences, "
        f"  min({_column_quoted('time')}) AS first_seen, "
        f"  max({_column_quoted('time')}) AS last_seen "
        f"FROM {_read_parquet_clause(globs)} "
        f"WHERE {where_sql} "
        f"GROUP BY 1 ORDER BY 2 DESC LIMIT {n}"
    )
    log.info(f"[find_error_patterns] service={application_service} top_n={n}")

    with _pool.acquire() as conn:
        rows = conn.execute(sql, params).fetchall()
        cols = [d[0] for d in conn.description]

    items = [dict(zip(cols, _serialize_row(row))) for row in rows]
    return {
        "success": True,
        "result": items,
        "count": len(items),
        "executionTime": time.time() - start_t,
    }


# ---------------- get_logs_by_trace_id ----------------

def get_logs_by_trace_id(
    trace_id: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    business_capability: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Procura `trace_id` em `args`, `extra-fields` ou `message` (regex fallback).

    Janela default: ultimas 1h. Se o usuário fornecer start/end, usa-os
    (ainda respeitando MAX_WINDOW_HOURS).
    """
    _ensure_initialized()
    start_t = time.time()
    if not trace_id:
        raise ValueError("'trace_id' is required")

    end_dt = _parse_time(end) if end else datetime.now(timezone.utc)
    start_dt = _parse_time(start) if start else (end_dt - timedelta(hours=1))
    globs = _globs_for_call(business_capability, start_dt, end_dt)

    # Tenta extrair trace_id de campos JSON ou texto. DuckDB aceita
    # try_cast/json_extract; mantemos múltiplos OR como fallback robusto.
    trace_id_safe = trace_id.replace("'", "")
    extra = _column_quoted("extra-fields")
    args_col = _column_quoted("args")
    msg = _column_quoted("message")

    where_extra = (
        f"(CAST({extra} AS VARCHAR) ILIKE '%{trace_id_safe}%' "
        f"OR CAST({args_col} AS VARCHAR) ILIKE '%{trace_id_safe}%' "
        f"OR {msg} ILIKE '%{trace_id_safe}%')"
    )
    where_sql, params = _common_filters(
        None, None, None, start_dt, end_dt, extra_clauses=[where_extra]
    )
    n = _coerce_limit(limit)

    sql = (
        f"SELECT {_column_quoted('time')} AS time, "
        f"{_column_quoted('level')} AS level, "
        f"{_column_quoted('message')} AS message, "
        f"{_BCAP_COL} AS business_capability, "
        f"{_APP_SVC_COL} AS application_service, "
        f"{args_col} AS args, "
        f"{extra} AS extra_fields "
        f"FROM {_read_parquet_clause(globs)} "
        f"WHERE {where_sql} "
        f"ORDER BY {_column_quoted('time')} ASC "
        f"LIMIT {n}"
    )
    log.info(
        f"[get_logs_by_trace_id] trace_id={trace_id[:16]}... | "
        f"capability={business_capability or '*'} | "
        f"window={start_dt.isoformat()}→{end_dt.isoformat()} | limit={n}"
    )

    with _pool.acquire() as conn:
        rows = conn.execute(sql, params).fetchall()
        cols = [d[0] for d in conn.description]

    items = [dict(zip(cols, _serialize_row(row))) for row in rows]
    return {
        "success": True,
        "result": items,
        "count": len(items),
        "trace_id": trace_id,
        "executionTime": time.time() - start_t,
    }


# ---------------- get_log_volume_timeline ----------------

def get_log_volume_timeline(
    application_service: Optional[str] = None,
    business_capability: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    step: str = "1h",
) -> Dict[str, Any]:
    """Volume de logs por bucket de tempo + level."""
    _ensure_initialized()
    start_t = time.time()
    if not start:
        raise ValueError("'start' is required")
    end_dt = _parse_time(end) if end else datetime.now(timezone.utc)
    start_dt = _parse_time(start)

    # Mapeia step → DuckDB time bucket interval. Usamos `date_trunc` quando o
    # step é uma unidade nativa (hour/day) e `time_bucket` para minutos com
    # `INTERVAL`. Evita dependência da extensão `icu` (pytz) sempre que possível.
    step_native = {
        "1h": "hour",
        "6h": "hour",
        "1d": "day",
    }
    if step in step_native:
        # `date_trunc('hour'|'day', t)` — sem pytz.
        unit = step_native[step]
        bucket_expr = f"date_trunc('{unit}', CAST({_column_quoted('time')} AS TIMESTAMP))"
    else:
        # Para minutos, usamos uma divisão inteira (epoch_seconds // step_seconds * step_seconds)
        # convertida de volta para timestamp via interval add. Mantém UTC e dispensa pytz.
        step_seconds_map = {"1m": 60, "5m": 300, "15m": 900}
        seconds = step_seconds_map.get(step, 3600)
        bucket_expr = (
            f"TIMESTAMP '1970-01-01 00:00:00' + INTERVAL (floor(epoch(CAST({_column_quoted('time')} AS TIMESTAMP)) / {seconds}) * {seconds}) SECOND"
        )

    globs = _globs_for_call(business_capability, start_dt, end_dt)
    where_sql, params = _common_filters(
        application_service, None, None, start_dt, end_dt
    )

    sql = (
        f"SELECT {bucket_expr} AS bucket, "
        f"  upper({_column_quoted('level')}) AS level, count(*) AS count "
        f"FROM {_read_parquet_clause(globs)} "
        f"WHERE {where_sql} "
        f"GROUP BY 1, 2 ORDER BY 1 ASC"
    )
    log.info(f"[get_log_volume_timeline] service={application_service} step={step}")

    with _pool.acquire() as conn:
        rows = conn.execute(sql, params).fetchall()

    items = [
        {"bucket": _serialize_value(r[0]), "level": r[1], "count": int(r[2])}
        for r in rows
    ]
    return {
        "success": True,
        "result": items,
        "step": step,
        "executionTime": time.time() - start_t,
    }


# ---------------- list_capabilities ----------------

def list_capabilities() -> Dict[str, Any]:
    """Lista valores `capability=<bcap>` disponíveis no bucket.

    Usa boto3 para listar prefixes (delimiter='/'). Não lê parquet —
    operação barata para descoberta.
    """
    _ensure_initialized()
    start_t = time.time()

    try:
        import boto3  # type: ignore
    except ImportError:
        return {"success": False, "error": "boto3 not installed"}

    creds = _creds.get()
    session = boto3.Session(
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        aws_session_token=creds["session_token"],
        region_name=creds["region"],
    )
    s3 = session.client("s3")
    paginator = s3.get_paginator("list_objects_v2")

    capabilities = []
    for page in paginator.paginate(Bucket=_config.bucket, Delimiter="/"):
        for prefix in page.get("CommonPrefixes", []) or []:
            p = prefix.get("Prefix", "")
            if p.startswith("capability=") and p.endswith("/"):
                capabilities.append(p[len("capability="): -1])

    capabilities.sort()
    log.info(f"[list_capabilities] discovered {len(capabilities)} capabilities")
    return {
        "success": True,
        "result": capabilities,
        "count": len(capabilities),
        "executionTime": time.time() - start_t,
    }
