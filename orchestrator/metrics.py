"""Prometheus metrics for the Observability Troubleshooting Copilot."""

from prometheus_client import Counter, Histogram, Gauge, Info

# ---------------------------------------------------------------------------
# Application info
# ---------------------------------------------------------------------------
APP_INFO = Info("observa", "Observability Troubleshooting Copilot")
APP_INFO.info({"version": "1.1.0", "service": "orchestrator"})

# ---------------------------------------------------------------------------
# Investigation metrics
# ---------------------------------------------------------------------------
INVESTIGATION_DURATION = Histogram(
    "observa_investigation_duration_seconds",
    "Time spent on a full investigation",
    ["input_type", "status"],
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120),
)

INVESTIGATION_TOTAL = Counter(
    "observa_investigation_total",
    "Total investigations executed",
    ["input_type", "status"],
)

EVIDENCE_COUNT = Histogram(
    "observa_evidence_count",
    "Number of evidence items gathered per investigation",
    ["input_type"],
    buckets=(0, 1, 5, 10, 25, 50, 100),
)

HYPOTHESIS_COUNT = Histogram(
    "observa_hypothesis_count",
    "Number of hypotheses generated per investigation",
    ["input_type"],
    buckets=(0, 1, 2, 5, 10),
)

CORRELATION_GAPS = Counter(
    "observa_correlation_gaps_total",
    "Total correlation gaps detected",
)

# ---------------------------------------------------------------------------
# MCP metrics
# ---------------------------------------------------------------------------
MCP_CALL_DURATION = Histogram(
    "observa_mcp_call_duration_seconds",
    "MCP server call duration",
    ["server", "tool", "status"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 15),
)

MCP_CALL_TOTAL = Counter(
    "observa_mcp_call_total",
    "Total MCP server calls",
    ["server", "tool", "status"],
)

# ---------------------------------------------------------------------------
# Chat / LLM metrics
# ---------------------------------------------------------------------------
CHAT_DURATION = Histogram(
    "observa_chat_duration_seconds",
    "Chat endpoint total duration",
    ["status"],
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120),
)

CHAT_TOTAL = Counter(
    "observa_chat_total",
    "Total chat requests",
    ["status"],
)

CHAT_SESSIONS_ACTIVE = Gauge(
    "observa_chat_sessions_active",
    "Number of active chat sessions",
)

LLM_CALL_DURATION = Histogram(
    "observa_llm_call_duration_seconds",
    "LLM API call duration",
    ["model", "status"],
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120),
)

LLM_CALL_TOTAL = Counter(
    "observa_llm_call_total",
    "Total LLM API calls",
    ["model", "status"],
)

LLM_TOKENS = Counter(
    "observa_llm_tokens_total",
    "Total LLM tokens consumed",
    ["model", "type"],  # type: prompt_tokens, completion_tokens, total_tokens
)

LLM_TOOL_CALLS = Counter(
    "observa_llm_tool_calls_total",
    "Total tool calls made by LLM",
    ["tool", "status"],
)

# ---------------------------------------------------------------------------
# PII redaction metrics
# ---------------------------------------------------------------------------
PII_REDACTIONS = Counter(
    "observa_pii_redactions_total",
    "Total PII redactions performed",
)
