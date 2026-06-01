"""Configuration dataclass and constants."""

import logging
import os
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Logging — fonte única do logger usado por todo o pacote.
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("logs-parquet-mcp")


# ---------------------------------------------------------------------------
# Limits (configuráveis via env, mas com defaults conservadores).
# ---------------------------------------------------------------------------

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
