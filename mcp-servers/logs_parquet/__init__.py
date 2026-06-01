"""Logs Parquet MCP Server — pacote modular.

Lê logs em formato Parquet armazenados em S3 com particionamento Hive
usando DuckDB embedded com a extensão httpfs.

Para rodar:
    python -m logs_parquet
"""

# Imports relativos para garantir que o pacote funcione independente do
# diretório de trabalho ou da forma como foi instalado.
from .config import (
    LogsConfig,
    MAX_WINDOW_HOURS,
    MAX_PARTITIONS,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    log,
)
from .aws_credentials import AWSCredentialsManager
from .duckdb_pool import DuckDBPool, SchemaCache
from .partitions import (
    _parse_time,
    _hours_in_range,
    _build_partition_globs,
    _filter_existing_globs,
    _build_globs_for_call,
)
from .tools import (
    search_logs,
    count_logs_by_level,
    find_error_patterns,
    get_logs_by_trace_id,
    get_log_volume_timeline,
    list_capabilities,
)
from .server import app, list_tools, call_tool, main_stdio, main_sse

__all__ = [
    # server / entrypoints
    "app",
    "list_tools",
    "call_tool",
    "main_stdio",
    "main_sse",
    # tools
    "search_logs",
    "count_logs_by_level",
    "find_error_patterns",
    "get_logs_by_trace_id",
    "get_log_volume_timeline",
    "list_capabilities",
    # core types
    "LogsConfig",
    "AWSCredentialsManager",
    "DuckDBPool",
    "SchemaCache",
    # partition helpers
    "_parse_time",
    "_hours_in_range",
    "_build_partition_globs",
    "_filter_existing_globs",
    "_build_globs_for_call",
    # constants / logger
    "MAX_WINDOW_HOURS",
    "MAX_PARTITIONS",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "log",
]
