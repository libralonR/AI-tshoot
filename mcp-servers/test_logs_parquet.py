"""Tests do logs-parquet-mcp.

Cria parquet local com colunas hifenizadas (igual produção), patcheia o
glob builder para apontar para tmpdir e exercita as 6 tools.

Não toca em S3 nem AWS — DuckDB embedded lê arquivos locais com particionamento
Hive, exatamente como faria em S3.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_parquet(tmpdir: Path) -> Path:
    """Cria um parquet de teste em tmpdir/capability=acquirer-c6pay/year=2024/...

    Os horários são gravados com offset -03:00 (igual produção) e a partição
    é em UTC (hour=05 para 02:00-03:00).
    """
    hive_dir = (
        tmpdir
        / "capability=acquirer-c6pay"
        / "year=2024"
        / "month=07"
        / "day=23"
        / "hour=05"
    )
    hive_dir.mkdir(parents=True)
    parquet_path = hive_dir / "test.parquet"

    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE logs AS
        SELECT * FROM (VALUES
            ('2024-07-23T02:01:00.000-03:00', 'INFO',  'Service started',                 'acquirer-c6pay', 'business-platform', 'c6pay-settlement', 'c6pay-receivables-service', '{}',                                  '{}'),
            ('2024-07-23T02:05:00.000-03:00', 'ERROR', 'Connection timeout to db-1',      'acquirer-c6pay', 'business-platform', 'c6pay-settlement', 'c6pay-receivables-service', '{"trace_id":"abc123def456"}',         '{}'),
            ('2024-07-23T02:10:00.000-03:00', 'ERROR', 'Connection timeout to db-2',      'acquirer-c6pay', 'business-platform', 'c6pay-settlement', 'c6pay-receivables-service', '{"trace_id":"abc123def456"}',         '{}'),
            ('2024-07-23T02:15:00.000-03:00', 'ERROR', 'Failed to process payment 99999', 'acquirer-c6pay', 'business-platform', 'c6pay-settlement', 'c6pay-receivables-service', '{}',                                  '{"trace_id":"xyz789"}'),
            ('2024-07-23T02:20:00.000-03:00', 'WARN',  'Retry attempt 3',                 'acquirer-c6pay', 'business-platform', 'c6pay-settlement', 'c6pay-receivables-service', '{}',                                  '{}'),
            ('2024-07-23T02:25:00.000-03:00', 'INFO',  'Health check OK',                 'acquirer-c6pay', 'business-platform', 'c6pay-settlement', 'c6pay-receivables-service', '{}',                                  '{}')
        ) AS t("time", "level", "message", "business-capability", "business-domain", "business-service", "application-service", "args", "extra-fields")
        """
    )
    con.execute(f"COPY logs TO '{parquet_path}' (FORMAT PARQUET)")
    con.close()
    return parquet_path


class _FakeCreds:
    def get(self):
        return {
            "access_key_id": None,
            "secret_access_key": None,
            "session_token": None,
            "region": "us-east-1",
        }


class LogsParquetTests(unittest.TestCase):
    """Suite de smoke + integração contra parquet local."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("LOGS_S3_BUCKET", "test-bucket")
        os.environ.setdefault("LOGS_AWS_REGION", "us-east-1")
        os.environ.setdefault("LOG_LEVEL", "WARNING")
        os.environ.setdefault("LOGS_MAX_WINDOW_HOURS", "24")
        os.environ.setdefault("LOGS_MAX_PARTITIONS", "48")

        cls.tmpdir = Path(tempfile.mkdtemp(prefix="logs_parquet_test_"))
        cls.parquet_path = _make_parquet(cls.tmpdir)

        # Importar somente após env e tmpdir prontos
        import logs_parquet

        cls.lp = logs_parquet

        # Patch do builder para apontar para tmpdir (file://)
        original_partition_globs = logs_parquet._build_partition_globs

        def patched(bucket, capabilities, hours):
            real = original_partition_globs(bucket, capabilities, hours)
            return [g.replace(f"s3://{bucket}", str(cls.tmpdir)) for g in real]

        logs_parquet._build_partition_globs = patched

        # Inicializa singletons e troca creds para fakes (DuckDB sem S3)
        logs_parquet._ensure_initialized()
        logs_parquet._creds = _FakeCreds()
        logs_parquet._pool._creds_applied_at = None

        # Janela cobrindo as horas 04, 05, 06 UTC (smoke do filter de globs)
        cls.start = "2024-07-23T04:00:00Z"
        cls.end = "2024-07-23T06:00:00Z"

    @classmethod
    def tearDownClass(cls):
        try:
            cls.lp._pool.close()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def test_search_logs_error_only(self):
        r = self.lp.search_logs(
            business_capability="acquirer-c6pay",
            level="ERROR",
            start=self.start, end=self.end, limit=10,
        )
        self.assertTrue(r["success"])
        self.assertEqual(r["count"], 3)
        for row in r["result"]:
            self.assertEqual(row["level"], "ERROR")

    def test_search_logs_text_match(self):
        r = self.lp.search_logs(
            business_capability="acquirer-c6pay",
            text_match="timeout",
            start=self.start, end=self.end,
        )
        self.assertTrue(r["success"])
        self.assertEqual(r["count"], 2)

    def test_search_logs_default_limit_capped(self):
        # limit > MAX_LIMIT deve ser capado
        r = self.lp.search_logs(
            business_capability="acquirer-c6pay",
            start=self.start, end=self.end, limit=999999,
        )
        self.assertTrue(r["success"])
        # Total de linhas é 6 — limit não vai limitar de fato, mas o coerce não pode estourar
        self.assertLessEqual(r["count"], 1000)

    def test_count_logs_by_level(self):
        r = self.lp.count_logs_by_level(
            business_capability="acquirer-c6pay",
            start=self.start, end=self.end,
        )
        self.assertTrue(r["success"])
        levels = {row["level"]: row["count"] for row in r["result"]}
        self.assertEqual(levels.get("ERROR"), 3)
        self.assertEqual(levels.get("INFO"), 2)
        self.assertEqual(levels.get("WARN"), 1)

    def test_find_error_patterns_groups_numbers(self):
        r = self.lp.find_error_patterns(
            application_service="c6pay-receivables-service",
            business_capability="acquirer-c6pay",
            start=self.start, end=self.end, top_n=5,
        )
        self.assertTrue(r["success"])
        patterns = {row["pattern"]: row["occurrences"] for row in r["result"]}
        self.assertEqual(len(r["result"]), 2)
        # "Connection timeout to db-1" e "db-2" colapsam em "db-<N>"
        norm_keys = list(patterns.keys())
        self.assertTrue(
            any("db-<N>" in p for p in norm_keys),
            f"Pattern normalization failed: {norm_keys}",
        )
        # E "payment 99999" → "payment <N>" → ocorre 1x
        self.assertTrue(any("payment <N>" in p for p in norm_keys))

    def test_get_logs_by_trace_id_in_args(self):
        r = self.lp.get_logs_by_trace_id(
            trace_id="abc123def456",
            business_capability="acquirer-c6pay",
            start=self.start, end=self.end,
        )
        self.assertTrue(r["success"])
        self.assertEqual(r["count"], 2)

    def test_get_logs_by_trace_id_in_extra_fields(self):
        r = self.lp.get_logs_by_trace_id(
            trace_id="xyz789",
            business_capability="acquirer-c6pay",
            start=self.start, end=self.end,
        )
        self.assertTrue(r["success"])
        self.assertEqual(r["count"], 1)

    def test_get_log_volume_timeline_no_pytz(self):
        # Garante que não exige pytz/icu (usa date_trunc, não time_bucket)
        r = self.lp.get_log_volume_timeline(
            business_capability="acquirer-c6pay",
            start=self.start, end=self.end, step="1h",
        )
        self.assertTrue(r["success"])
        self.assertEqual(r["step"], "1h")
        self.assertGreaterEqual(len(r["result"]), 3)  # ERROR/INFO/WARN

    def test_get_log_volume_timeline_minute_bucket(self):
        r = self.lp.get_log_volume_timeline(
            business_capability="acquirer-c6pay",
            start=self.start, end=self.end, step="5m",
        )
        self.assertTrue(r["success"])

    # ------------------------------------------------------------------
    # Limites
    # ------------------------------------------------------------------

    def test_window_over_24h_rejected(self):
        with self.assertRaises(ValueError):
            self.lp.search_logs(
                start="2024-07-01T00:00:00Z",
                end="2024-07-10T00:00:00Z",
            )

    def test_missing_start_rejected(self):
        with self.assertRaises(ValueError):
            self.lp.search_logs(business_capability="acquirer-c6pay")

    def test_find_error_patterns_requires_service(self):
        with self.assertRaises(ValueError):
            self.lp.find_error_patterns(
                application_service="",
                start=self.start, end=self.end,
            )

    # ------------------------------------------------------------------
    # MCP integration
    # ------------------------------------------------------------------

    def test_list_tools_has_six(self):
        tools = asyncio.run(self.lp.list_tools())
        names = [t["name"] for t in tools]
        self.assertEqual(len(tools), 6)
        self.assertIn("search_logs", names)
        self.assertIn("count_logs_by_level", names)
        self.assertIn("find_error_patterns", names)
        self.assertIn("get_logs_by_trace_id", names)
        self.assertIn("get_log_volume_timeline", names)
        self.assertIn("list_capabilities", names)

    def test_call_tool_success_payload(self):
        result = asyncio.run(self.lp.call_tool("search_logs", {
            "business_capability": "acquirer-c6pay",
            "level": "ERROR",
            "start": self.start, "end": self.end,
        }))
        self.assertEqual(len(result), 1)
        parsed = json.loads(result[0]["text"])
        self.assertTrue(parsed["success"])
        self.assertEqual(parsed["count"], 3)
        self.assertIn("executionTime", parsed)

    def test_call_tool_error_propagates(self):
        result = asyncio.run(self.lp.call_tool("search_logs", {
            "start": "2024-07-01T00:00:00Z",
            "end": "2024-07-10T00:00:00Z",
        }))
        parsed = json.loads(result[0]["text"])
        self.assertFalse(parsed["success"])
        self.assertIn("Time window exceeds limit", parsed["error"])

    def test_call_tool_unknown_tool(self):
        result = asyncio.run(self.lp.call_tool("nonexistent", {}))
        parsed = json.loads(result[0]["text"])
        self.assertFalse(parsed["success"])
        self.assertIn("Unknown tool", parsed["error"])

    # ------------------------------------------------------------------
    # Time parsing
    # ------------------------------------------------------------------

    def test_time_parse_iso_utc(self):
        dt = self.lp._parse_time("2024-07-23T05:00:00Z")
        self.assertEqual(dt.isoformat(), "2024-07-23T05:00:00+00:00")

    def test_time_parse_iso_with_offset(self):
        dt = self.lp._parse_time("2024-07-23T02:00:00-03:00")
        # Convertido para UTC
        self.assertEqual(dt.isoformat(), "2024-07-23T05:00:00+00:00")

    def test_time_parse_epoch_ms(self):
        dt = self.lp._parse_time(1721707200000)  # 2024-07-23T04:00:00Z
        self.assertEqual(dt.isoformat(), "2024-07-23T04:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
