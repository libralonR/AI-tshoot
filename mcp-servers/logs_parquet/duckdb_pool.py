"""DuckDB connection pool and schema cache."""

import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import duckdb

from .config import LogsConfig, SCHEMA_CACHE_TTL, _CANONICAL_COLUMNS
from .aws_credentials import AWSCredentialsManager

log = logging.getLogger("logs-parquet-mcp")


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
