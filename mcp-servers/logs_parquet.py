#!/usr/bin/env python3
"""
Logs Parquet MCP Server.

Lê logs em formato Parquet armazenados em S3 com particionamento Hive
(`capability=<bcap>/year=YYYY/month=MM/day=DD/hour=HH/*.parquet`) usando
DuckDB embedded com a extensão httpfs.

- Read-only por design (nenhuma escrita em S3 ou no DuckDB).
- Autenticação via AssumeRole (STS) com refresh automático antes de expirar.
- Modo dual: stdio (MCP nativo) ou sse (REST via FastAPI/Starlette) — igual aos
  outros MCPs Python do projeto (incidents_pg, grafana_v2, victoriametrics_mcp).
- Limites duros para proteger o pod:
    * janela máxima 24h
    * scan máximo 24 partições (1h cada → 1 dia inteiro)
    * limit padrão 500 linhas, máximo 1000
"""

import asyncio
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

import duckdb
from mcp.server import Server
from mcp.server.stdio import stdio_server

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("logs-parquet-mcp")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Limites duros (configuráveis via env, mas com defaults conservadores).
MAX_WINDOW_HOURS = int(os.getenv("LOGS_MAX_WINDOW_HOURS", "24"))
MAX_PARTITIONS = int(os.getenv("LOGS_MAX_PARTITIONS", "24"))  # 1h cada → 24h
DEFAULT_LIMIT = int(os.getenv("LOGS_DEFAULT_LIMIT", "500"))
MAX_LIMIT = int(os.getenv("LOGS_MAX_LIMIT", "1000"))
SCHEMA_CACHE_TTL = int(os.getenv("LOGS_SCHEMA_CACHE_TTL_SECONDS", "3600"))


@dataclass(frozen=True)
class LogsConfig:
    bucket: str
    aws_region: str
    role_arn: Optional[str]
    role_session_name: str
    duckdb_threads: int

    @staticmethod
    def from_env() -> "LogsConfig":
        bucket = os.getenv("LOGS_S3_BUCKET", "observability-data-log").strip()
        if not bucket:
            raise RuntimeError("LOGS_S3_BUCKET env var is required")
        return LogsConfig(
            bucket=bucket,
            aws_region=os.getenv("LOGS_AWS_REGION", "us-east-1"),
            role_arn=os.getenv("LOGS_ROLE_ARN") or None,
            role_session_name=os.getenv("LOGS_ROLE_SESSION_NAME", "logs-parquet-mcp"),
            duckdb_threads=int(os.getenv("LOGS_DUCKDB_THREADS", "4")),
        )


# ---------------------------------------------------------------------------
# AWS credentials manager (AssumeRole + refresh)
# ---------------------------------------------------------------------------

class AWSCredentialsManager:
    """Gerencia credenciais AWS para acesso ao S3 via DuckDB.

    Estratégia:
      1. Se `LOGS_ROLE_ARN` definido → usa STS AssumeRole, refresh ~10min antes
         de expirar.
      2. Se IRSA (web identity token), boto3 detecta automaticamente.
      3. Senão, usa credenciais default da boto3 (env vars, instance profile,
         ~/.aws/credentials, etc).
    """

    REFRESH_MARGIN = timedelta(minutes=10)

    def __init__(self, cfg: LogsConfig):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._access_key: Optional[str] = None
        self._secret_key: Optional[str] = None
        self._session_token: Optional[str] = None
        self._expiration: Optional[datetime] = None

    def get(self) -> Dict[str, Optional[str]]:
        """Retorna dict com credenciais válidas, refrescando se necessário."""
        with self._lock:
            if self._needs_refresh():
                self._refresh()
            return {
                "access_key_id": self._access_key,
                "secret_access_key": self._secret_key,
                "session_token": self._session_token,
                "region": self.cfg.aws_region,
            }

    def _needs_refresh(self) -> bool:
        if self._access_key is None:
            return True
        if self._expiration is None:
            # Credencial estática (sem expiração) — não precisa refresh.
            return False
        return datetime.now(timezone.utc) + self.REFRESH_MARGIN >= self._expiration

    def _refresh(self) -> None:
        # boto3 é importado lazy para não ser obrigatório em modo offline.
        try:
            import boto3  # type: ignore
        except ImportError as exc:  # pragma: no cover - env should have boto3
            raise RuntimeError(
                "boto3 not installed; required for LOGS_ROLE_ARN AssumeRole"
            ) from exc

        if self.cfg.role_arn:
            log.info(
                f"[AWSCredentialsManager] AssumeRole role_arn={self.cfg.role_arn} "
                f"session={self.cfg.role_session_name}"
            )
            sts = boto3.client("sts", region_name=self.cfg.aws_region)
            resp = sts.assume_role(
                RoleArn=self.cfg.role_arn,
                RoleSessionName=self.cfg.role_session_name,
                DurationSeconds=int(os.getenv("LOGS_ROLE_DURATION_SECONDS", "3600")),
            )
            creds = resp["Credentials"]
            self._access_key = creds["AccessKeyId"]
            self._secret_key = creds["SecretAccessKey"]
            self._session_token = creds["SessionToken"]
            self._expiration = creds["Expiration"]
            log.info(
                f"[AWSCredentialsManager] credentials refreshed | "
                f"expires_at={self._expiration.isoformat()}"
            )
            return

        # Sem role_arn → tenta credenciais default da chain do boto3.
        session = boto3.Session(region_name=self.cfg.aws_region)
        creds = session.get_credentials()
        if creds is None:
            raise RuntimeError(
                "No AWS credentials available. Set LOGS_ROLE_ARN or configure "
                "default credential chain (IRSA, env vars, instance profile)."
            )
        frozen = creds.get_frozen_credentials()
        self._access_key = frozen.access_key
        self._secret_key = frozen.secret_key
        self._session_token = frozen.token
        # boto3 default chain pode não expor expiração — assume estático.
        self._expiration = None
        log.info(
            f"[AWSCredentialsManager] using default credential chain | "
            f"access_key=...{self._access_key[-4:] if self._access_key else 'None'}"
        )


# ---------------------------------------------------------------------------
# DuckDB session
# ---------------------------------------------------------------------------

class DuckDBPool:
    """Pool simples (1 conexão sob lock) configurado com httpfs + credenciais.

    DuckDB embedded é thread-safe quando usado dentro de um único processo,
    mas as queries não são paralelas dentro da mesma conexão. Para o MCP
    (request-per-tool), 1 conexão com lock é suficiente. Pode ser elevado
    para múltiplas conexões se virar gargalo.
    """

    def __init__(self, cfg: LogsConfig, creds_manager: AWSCredentialsManager):
        self.cfg = cfg
        self.creds_manager = creds_manager
        self._lock = threading.Lock()
        self._conn: Optional[duckdb.DuckDBPyConnection] = None
        self._creds_applied_at: Optional[datetime] = None

    def _connect(self) -> duckdb.DuckDBPyConnection:
        log.info("[DuckDBPool] opening DuckDB connection (in-memory)")
        conn = duckdb.connect(database=":memory:")
        conn.execute(f"PRAGMA threads={self.cfg.duckdb_threads}")

        # Usar diretório de extensões customizado se configurado (pré-instalado no Docker build)
        ext_dir = os.environ.get("DUCKDB_EXTENSION_DIRECTORY")
        if ext_dir:
            conn.execute(f"SET extension_directory='{ext_dir}'")
            log.info(f"[DuckDBPool] extension_directory={ext_dir}")

        # Carrega extensão httpfs. Tenta INSTALL primeiro (para ambientes onde
        # a extensão não foi pré-instalada no build). Se falhar (HTTP 403,
        # offline, etc.), tenta apenas LOAD (funciona se já foi instalada
        # durante o docker build ou em execução anterior).
        try:
            conn.execute("INSTALL httpfs")
        except Exception as exc:  # noqa: BLE001
            log.warning(
                f"[DuckDBPool] INSTALL httpfs failed ({type(exc).__name__}: {str(exc)[:100]}); "
                f"attempting LOAD only (extension may be pre-installed)"
            )
        try:
            conn.execute("LOAD httpfs")
        except Exception as exc:  # noqa: BLE001
            log.error(
                f"[DuckDBPool] LOAD httpfs failed — S3 access will not work. "
                f"Ensure httpfs is pre-installed in the Docker image. Error: {exc}"
            )
        return conn

    def _apply_credentials(self, conn: duckdb.DuckDBPyConnection) -> None:
        creds = self.creds_manager.get()
        conn.execute(f"SET s3_region='{creds['region']}'")
        if creds["access_key_id"]:
            conn.execute(f"SET s3_access_key_id='{creds['access_key_id']}'")
        if creds["secret_access_key"]:
            conn.execute(f"SET s3_secret_access_key='{creds['secret_access_key']}'")
        if creds["session_token"]:
            conn.execute(f"SET s3_session_token='{creds['session_token']}'")
        else:
            # Ao trocar de credenciais com session_token para sem, é preciso
            # explicitamente limpar o token antigo.
            conn.execute("SET s3_session_token=''")
        self._creds_applied_at = datetime.now(timezone.utc)
        log.debug("[DuckDBPool] credentials applied to DuckDB session")

    @contextmanager
    def acquire(self):
        """Context manager que entrega uma conexão pronta para uso."""
        with self._lock:
            if self._conn is None:
                self._conn = self._connect()
            # Reaplica credenciais se faz mais de 5min ou se nunca foram setadas.
            now = datetime.now(timezone.utc)
            if (
                self._creds_applied_at is None
                or (now - self._creds_applied_at) > timedelta(minutes=5)
            ):
                self._apply_credentials(self._conn)
            yield self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None


# ---------------------------------------------------------------------------
# Schema discovery (cache)
# ---------------------------------------------------------------------------

class SchemaCache:
    """Descoberta de schema do parquet (sem Glue) via SELECT em 1 arquivo."""

    def __init__(self, pool: DuckDBPool):
        self.pool = pool
        self._lock = threading.Lock()
        self._columns: Optional[List[str]] = None
        self._fetched_at: Optional[datetime] = None
        self._sample_path: Optional[str] = None

    def get_columns(self, sample_path: Optional[str] = None) -> List[str]:
        """Retorna lista de colunas. Faz lookup na primeira chamada e cacheia."""
        with self._lock:
            if self._columns and self._is_fresh():
                return self._columns
            if not sample_path:
                # Sem amostra, retorna o conjunto canônico esperado.
                return _CANONICAL_COLUMNS
            try:
                with self.pool.acquire() as conn:
                    rows = conn.execute(
                        f"DESCRIBE SELECT * FROM read_parquet('{sample_path}')"
                    ).fetchall()
                self._columns = [r[0] for r in rows]
                self._fetched_at = datetime.now(timezone.utc)
                self._sample_path = sample_path
                log.info(
                    f"[SchemaCache] discovered {len(self._columns)} columns from "
                    f"{sample_path}: {self._columns}"
                )
                return self._columns
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[SchemaCache] discovery failed for {sample_path}: {exc}")
                return _CANONICAL_COLUMNS

    def _is_fresh(self) -> bool:
        if not self._fetched_at:
            return False
        return (datetime.now(timezone.utc) - self._fetched_at) < timedelta(
            seconds=SCHEMA_CACHE_TTL
        )


_CANONICAL_COLUMNS = [
    "time",
    "level",
    "message",
    "business-capability",
    "business-domain",
    "business-service",
    "application-service",
    "args",
    "extra-fields",
]


# ---------------------------------------------------------------------------
# Partition helpers
# ---------------------------------------------------------------------------

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _parse_time(value: str) -> datetime:
    """Aceita ISO 8601 (com ou sem timezone) ou epoch_ms."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    s = str(value).strip()
    if s.isdigit():
        return datetime.fromtimestamp(int(s) / 1000.0, tz=timezone.utc)
    # Aceita "Z" e offset com colon
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f"Invalid time format: {value!r} (expected ISO 8601 or epoch_ms)") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hours_in_range(start: datetime, end: datetime) -> List[datetime]:
    """Lista de horas (UTC) cobrindo o intervalo [start, end].

    Sempre inclui a hora de start e end. Falha se exceder MAX_PARTITIONS.
    """
    if end < start:
        raise ValueError("end must be >= start")
    if (end - start) > timedelta(hours=MAX_WINDOW_HOURS):
        raise ValueError(
            f"Time window exceeds limit of {MAX_WINDOW_HOURS}h. "
            f"start={start.isoformat()} end={end.isoformat()}"
        )
    cursor = start.replace(minute=0, second=0, microsecond=0)
    end_floor = end.replace(minute=0, second=0, microsecond=0)
    hours = []
    while cursor <= end_floor:
        hours.append(cursor)
        cursor += timedelta(hours=1)
    if len(hours) > MAX_PARTITIONS:
        raise ValueError(
            f"Time window expands to {len(hours)} partitions, max is {MAX_PARTITIONS}"
        )
    return hours


def _build_partition_globs(
    bucket: str,
    capabilities: Iterable[str],
    hours: Iterable[datetime],
) -> List[str]:
    """Gera lista de globs s3://bucket/capability=X/year=.../*.parquet."""
    caps = [c.strip() for c in capabilities if c and c.strip()]
    if not caps:
        # Sem capability conhecida → wildcard (busca cara, mas legítima).
        caps = ["*"]
    globs = []
    for cap in caps:
        cap_part = "*" if cap == "*" else f"capability={cap}"
        for h in hours:
            glob = (
                f"s3://{bucket}/{cap_part}/"
                f"year={h.year:04d}/month={h.month:02d}/day={h.day:02d}/hour={h.hour:02d}/*.parquet"
            )
            globs.append(glob)
    return globs


# ---------------------------------------------------------------------------
# Globals
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


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _coerce_limit(value: Any) -> int:
    try:
        n = int(value) if value is not None else DEFAULT_LIMIT
    except (ValueError, TypeError):
        n = DEFAULT_LIMIT
    return max(1, min(n, MAX_LIMIT))


def _column_quoted(name: str) -> str:
    """Envolve nomes com hífen em aspas duplas para DuckDB."""
    return f'"{name}"' if "-" in name else name


_BCAP_COL = _column_quoted("business-capability")
_APP_SVC_COL = _column_quoted("application-service")
_BSVC_COL = _column_quoted("business-service")
_BDOMAIN_COL = _column_quoted("business-domain")


def _normalize_capabilities(business_capability: Optional[str]) -> List[str]:
    """Normaliza para lista (suporta CSV). Retorna lista vazia se None."""
    if not business_capability:
        return []
    return [c.strip() for c in business_capability.split(",") if c.strip()]


def _build_globs_for_call(
    business_capability: Optional[str],
    start_dt: datetime,
    end_dt: datetime,
) -> List[str]:
    caps = _normalize_capabilities(business_capability) or ["*"]
    hours = _hours_in_range(start_dt, end_dt)
    raw = _build_partition_globs(_config.bucket, caps, hours)
    surviving = _filter_existing_globs(raw)
    log.info(
        f"[_build_globs_for_call] caps={caps} | hours={len(hours)} | "
        f"raw_globs={len(raw)} | surviving={len(surviving)}"
    )
    return surviving


def _read_parquet_clause(globs: List[str]) -> str:
    """Monta o clause `read_parquet([...], hive_partitioning=true)`.

    DuckDB lança erro se algum glob não casar com nenhum arquivo. Em
    janelas que cobrem o "agora" pode haver hour ainda sem dados, e em
    capabilities que não geraram log na janela. O caller deve filtrar
    globs vazios antes (via boto3 list_objects_v2 para S3 ou os.path
    para file://). Quando a lista resulta vazia, retornamos um SELECT
    sintético vazio para preservar o shape do resultado.
    """
    if not globs:
        return (
            "(SELECT NULL::VARCHAR AS \"time\", NULL::VARCHAR AS \"level\", "
            "NULL::VARCHAR AS \"message\", NULL::VARCHAR AS \"business-capability\", "
            "NULL::VARCHAR AS \"business-domain\", NULL::VARCHAR AS \"business-service\", "
            "NULL::VARCHAR AS \"application-service\", NULL::VARCHAR AS \"args\", "
            "NULL::VARCHAR AS \"extra-fields\" WHERE 1=0)"
        )
    quoted = ", ".join(f"'{g}'" for g in globs)
    return f"read_parquet([{quoted}], hive_partitioning=true, union_by_name=true)"


def _filter_existing_globs(globs: List[str]) -> List[str]:
    """Remove globs que não casam com nenhum arquivo.

    - Para `file://` ou paths absolutos: usa glob.glob.
    - Para `s3://`: usa boto3 list_objects_v2 (apenas se boto3 disponível).
    - Em caso de erro/timeout no S3, retorna o glob original (DuckDB tenta).
    """
    import glob as glob_module

    surviving: List[str] = []
    s3_globs: List[str] = []
    for g in globs:
        if g.startswith("s3://"):
            s3_globs.append(g)
        else:
            if glob_module.glob(g):
                surviving.append(g)

    if s3_globs:
        try:
            import boto3  # type: ignore

            creds = _creds.get() if _creds else {}
            session = boto3.Session(
                aws_access_key_id=creds.get("access_key_id"),
                aws_secret_access_key=creds.get("secret_access_key"),
                aws_session_token=creds.get("session_token"),
                region_name=creds.get("region", _config.aws_region if _config else None),
            )
            s3 = session.client("s3")
            for g in s3_globs:
                # s3://bucket/path/with/year=Y/month=M/.../*.parquet
                # remove o "*.parquet" final e usa o restante como Prefix
                prefix = g.replace("s3://", "", 1)
                bucket, _, key = prefix.partition("/")
                key_prefix = key.rsplit("/", 1)[0] + "/"
                # Lista até 1 objeto para confirmar que existe algo
                try:
                    resp = s3.list_objects_v2(
                        Bucket=bucket, Prefix=key_prefix, MaxKeys=1
                    )
                    if resp.get("KeyCount", 0) > 0:
                        surviving.append(g)
                    else:
                        log.debug(f"[_filter_existing_globs] S3 prefix vazio: {key_prefix}")
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        f"[_filter_existing_globs] S3 list_objects_v2 falhou em {key_prefix}: {exc}; "
                        f"mantendo glob (DuckDB vai tentar)"
                    )
                    surviving.append(g)
        except ImportError:
            # Sem boto3 → mantém todos (DuckDB lança erro se vazio).
            surviving.extend(s3_globs)

    return surviving


def _common_filters(
    application_service: Optional[str],
    level: Optional[str],
    text_match: Optional[str],
    start_dt: datetime,
    end_dt: datetime,
    extra_clauses: Optional[List[str]] = None,
) -> tuple[str, list]:
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
    globs = _build_globs_for_call(business_capability, start_dt, end_dt)
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

    globs = _build_globs_for_call(business_capability, start_dt, end_dt)
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

    globs = _build_globs_for_call(business_capability, start_dt, end_dt)
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
    globs = _build_globs_for_call(business_capability, start_dt, end_dt)

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

    globs = _build_globs_for_call(business_capability, start_dt, end_dt)
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


# ---------------- helpers ----------------

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
# MCP server (stdio + REST)
# ---------------------------------------------------------------------------

app = Server("logs-parquet-mcp")


@app.list_tools()
async def list_tools() -> list[dict]:
    return [
        {
            "name": "search_logs",
            "description": (
                "Busca livre em logs forenses (S3 Parquet). Filtros opcionais por "
                "application_service, business_capability, level, text_match. "
                "Janela start/end obrigatória (max 24h)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "application_service": {"type": "string"},
                    "business_capability": {
                        "type": "string",
                        "description": "Capability do path (ex: acquirer-c6pay). Pode ser CSV.",
                    },
                    "level": {"type": "string", "description": "ERROR, WARN, INFO, DEBUG"},
                    "text_match": {"type": "string", "description": "ILIKE %text%"},
                    "start": {"type": "string", "description": "ISO 8601 ou epoch_ms"},
                    "end": {"type": "string", "description": "ISO 8601 ou epoch_ms (default: agora)"},
                    "limit": {"type": "integer", "description": f"Default {DEFAULT_LIMIT}, max {MAX_LIMIT}"},
                },
                "required": ["start"],
            },
        },
        {
            "name": "count_logs_by_level",
            "description": "Contagem agregada de logs por level na janela.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "application_service": {"type": "string"},
                    "business_capability": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                },
                "required": ["start"],
            },
        },
        {
            "name": "find_error_patterns",
            "description": (
                "Top error patterns por frequência (level=ERROR). Mensagens são "
                "normalizadas (números, UUIDs, strings) para agrupar variantes."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "application_service": {"type": "string"},
                    "business_capability": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "top_n": {"type": "integer"},
                },
                "required": ["application_service", "start"],
            },
        },
        {
            "name": "get_logs_by_trace_id",
            "description": (
                "Recupera logs cujo conteúdo (em args, extra-fields ou message) "
                "contém o trace_id. Janela default: última 1h."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "trace_id": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "business_capability": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["trace_id"],
            },
        },
        {
            "name": "get_log_volume_timeline",
            "description": "Timeline de volume de logs agrupado por bucket de tempo e level.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "application_service": {"type": "string"},
                    "business_capability": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "step": {
                        "type": "string",
                        "description": "1m | 5m | 15m | 1h | 6h | 1d (default 1h)",
                    },
                },
                "required": ["start"],
            },
        },
        {
            "name": "list_capabilities",
            "description": "Lista valores capability=<bcap> presentes no bucket S3.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


_TOOL_DISPATCH = {
    "search_logs": search_logs,
    "count_logs_by_level": count_logs_by_level,
    "find_error_patterns": find_error_patterns,
    "get_logs_by_trace_id": get_logs_by_trace_id,
    "get_log_volume_timeline": get_log_volume_timeline,
    "list_capabilities": list_capabilities,
}


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[dict]:
    log.info(f"Tool called: {name} with arguments: {arguments}")
    start_time = time.time()
    try:
        fn = _TOOL_DISPATCH.get(name)
        if fn is None:
            raise ValueError(f"Unknown tool: {name}")
        # Tools são síncronas (DuckDB) — rodar em threadpool para não bloquear
        # o event loop quando chamadas via MCP/SSE.
        result = await asyncio.to_thread(fn, **arguments)
        result.setdefault("executionTime", time.time() - start_time)
        return [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]
    except Exception as e:  # noqa: BLE001
        log.exception(f"Tool {name} failed")
        error_result = {
            "success": False,
            "error": str(e),
            "executionTime": time.time() - start_time,
        }
        return [{"type": "text", "text": json.dumps(error_result, indent=2)}]


# ---------------------------------------------------------------------------
# Modes: stdio vs sse (REST)
# ---------------------------------------------------------------------------

async def main_stdio():
    log.info("Starting Logs Parquet MCP Server in stdio mode")
    _ensure_initialized()
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


def main_sse():
    """Run in SSE mode with REST endpoints (Docker / K8s)."""
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route
    from mcp.server.sse import SseServerTransport
    import uvicorn

    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await app.run(streams[0], streams[1], app.create_initialization_options())

    async def handle_health(request):
        try:
            _ensure_initialized()
            return JSONResponse({
                "status": "healthy",
                "service": "logs-parquet-mcp",
                "backend": "duckdb",
                "bucket": _config.bucket if _config else None,
                "region": _config.aws_region if _config else None,
                "role_arn": _config.role_arn if _config else None,
                "max_window_hours": MAX_WINDOW_HOURS,
                "max_partitions": MAX_PARTITIONS,
            })
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {"status": "unhealthy", "error": str(exc)}, status_code=503
            )

    async def handle_list_tools_endpoint(request):
        tools = await list_tools()
        return JSONResponse({"tools": tools})

    async def handle_tool_call(request: Request):
        tool_name = request.path_params["tool_name"]
        body = await request.json()
        arguments = body.get("arguments", {})
        log.info(f"REST /tools/{tool_name} called with: {arguments}")
        try:
            result = await call_tool(tool_name, arguments)
            text = result[0]["text"] if result else "{}"
            parsed = json.loads(text)
            log.info(
                f"REST /tools/{tool_name} success={parsed.get('success')} "
                f"executionTime={parsed.get('executionTime'):.3f}s"
            )
            status = 200 if parsed.get("success") else 500
            return JSONResponse(parsed, status_code=status)
        except Exception as e:  # noqa: BLE001
            log.exception(f"REST /tools/{tool_name} error: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

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
    log.info(f"Starting Logs Parquet MCP Server in SSE/REST mode on port {port}")
    _ensure_initialized()
    uvicorn.run(starlette_app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    mode = os.getenv("MCP_SERVER_MODE", "sse").lower()
    log.info(f"Starting logs_parquet.py in mode={mode}")
    log.info(f"ENV: LOGS_S3_BUCKET={os.getenv('LOGS_S3_BUCKET', 'NOT SET')}")
    log.info(f"ENV: LOGS_AWS_REGION={os.getenv('LOGS_AWS_REGION', 'NOT SET')}")
    log.info(
        f"ENV: LOGS_ROLE_ARN={'SET' if os.getenv('LOGS_ROLE_ARN') else 'NOT SET (using default chain)'}"
    )
    log.info(f"ENV: MCP_SERVER_MODE={mode}")

    if mode == "sse":
        main_sse()
    else:
        asyncio.run(main_stdio())
