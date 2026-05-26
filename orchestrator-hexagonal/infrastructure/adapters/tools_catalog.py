"""Catálogo de tools que o LLM pode invocar via function calling.

Idêntico ao definido no `orchestrator/llm_client.py` da versão atual.
Mantido em arquivo separado para facilitar evolução e mock em testes.
"""

AVAILABLE_TOOLS = [
    # ------------------------------------------------------------------
    # Grafana Tempo (TraceQL) tools
    # ------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "traceql-search",
            "description": "Search for traces using TraceQL queries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "TraceQL query string"},
                    "limit": {"type": "integer", "description": "Maximum number of traces to return (default: 20)"},
                    "start": {"type": "string", "description": "Start time (ISO 8601 or epoch ms)"},
                    "end": {"type": "string", "description": "End time (ISO 8601 or epoch ms)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "traceql-metrics-instant",
            "description": "Retrieve a single metric value given a TraceQL metrics query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "TraceQL metrics query string"},
                    "time": {"type": "string", "description": "Evaluation timestamp (ISO 8601 or epoch ms)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "traceql-metrics-range",
            "description": "Retrieve a metric series given a TraceQL metrics query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "TraceQL metrics query string"},
                    "start": {"type": "string", "description": "Start time (ISO 8601 or epoch ms)"},
                    "end": {"type": "string", "description": "End time (ISO 8601 or epoch ms)"},
                    "step": {"type": "string", "description": "Query resolution step (e.g. '1m', '5m', '1h')"},
                },
                "required": ["query", "start"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get-trace",
            "description": "Retrieve a specific trace by ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trace_id": {"type": "string", "description": "Trace ID to retrieve"},
                },
                "required": ["trace_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get-attribute-names",
            "description": "Get available attribute names for use in TraceQL queries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "description": "Attribute scope (e.g. 'resource', 'span', 'event', 'link')"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get-attribute-values",
            "description": "Get values for a specific scoped attribute name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "description": "Attribute scope"},
                    "attribute": {"type": "string", "description": "Attribute name to get values for"},
                },
                "required": ["scope", "attribute"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docs-traceql",
            "description": "Retrieve TraceQL documentation (basic, aggregates, structural, metrics).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    # ------------------------------------------------------------------
    # Grafana MCP tools
    # ------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "find_firing_alerts",
            "description": "Find currently firing alerts in Grafana. Use when user asks about alerts, incidents, or problems.",
            "parameters": {
                "type": "object",
                "properties": {
                    "application_service": {"type": "string", "description": "Filter by service/component name"},
                    "owner_squad": {"type": "string", "description": "Filter by responsible squad"},
                    "severidade": {"type": "string", "description": "Filter by severity: P1, P2, P3"},
                    "business_capability": {"type": "string", "description": "Filter by business capability"},
                    "alertname": {"type": "string", "description": "Filter by alert rule name"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_alert_details",
            "description": "Get details of a specific Grafana alert by its UID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "alertUID": {"type": "string", "description": "The Grafana alert rule UID"},
                },
                "required": ["alertUID"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_incident",
            "description": "Get incident details by number (e.g. INC0012345).",
            "parameters": {
                "type": "object",
                "properties": {
                    "number": {"type": "string", "description": "Incident number (e.g. INC0012345)"},
                },
                "required": ["number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_incidents",
            "description": "Search incidents by filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "application_service": {"type": "string", "description": "Service/component name"},
                    "priority": {"type": "string", "description": "Priority: 1, 2, 3, 4"},
                    "state": {"type": "string", "description": "State: New, In Progress, Resolved, Closed"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_dashboards",
            "description": "Find Grafana dashboards by labels or tags.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Dashboard tags to filter by",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_related_incidents",
            "description": "Find incidents related to a specific incident or service. Supports filtering by multiple Grafana labels in the description field.",
            "parameters": {
                "type": "object",
                "properties": {
                    "number": {"type": "string", "description": "Reference incident number (e.g. INC0012345)"},
                    "application_service": {"type": "string", "description": "Service name to find related incidents"},
                    "business_capability": {"type": "string", "description": "Business capability to filter incidents"},
                    "business_domain": {"type": "string", "description": "Business domain to filter incidents"},
                    "business_service": {"type": "string", "description": "Business service to filter incidents"},
                    "owner_squad": {"type": "string", "description": "Owner squad to filter incidents"},
                    "owner_sre": {"type": "string", "description": "Owner SRE to filter incidents"},
                    "time_window_hours": {"type": "integer", "description": "Time window in hours (default: 24)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_incident_stats",
            "description": "Get incident statistics grouped by priority, category, state, or assignment_group.",
            "parameters": {
                "type": "object",
                "properties": {
                    "application_service": {"type": "string", "description": "Filter by service name"},
                    "days": {"type": "integer", "description": "Period in days (default: 30)"},
                    "group_by": {
                        "type": "string",
                        "enum": ["priority", "category", "state", "assignment_group_name"],
                        "description": "Group results by this field",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_panel_link",
            "description": "Generate a direct link to a specific Grafana dashboard panel with time range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dashboardUID": {"type": "string", "description": "Dashboard UID"},
                    "panelId": {"type": "integer", "description": "Panel ID"},
                    "timeRange": {
                        "type": "object",
                        "properties": {
                            "start": {"type": "integer", "description": "Start time in epoch ms"},
                            "end": {"type": "integer", "description": "End time in epoch ms"},
                        },
                    },
                },
                "required": ["dashboardUID", "panelId"],
            },
        },
    },
    # ------------------------------------------------------------------
    # VictoriaMetrics MCP tools (via vm_mcp_proxy ou nativo)
    # ------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "query",
            "description": "Execute an instant PromQL/MetricsQL query against VictoriaMetrics. Returns current metric values.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "PromQL or MetricsQL expression (e.g. 'up', 'rate(http_requests_total[5m])')",
                    },
                    "time": {"type": "string", "description": "Evaluation timestamp (ISO 8601 or epoch ms). Defaults to now."},
                    "step": {"type": "string", "description": "Lookback interval for raw samples (e.g. '5m'). Default: 5m."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_range",
            "description": "Execute a range PromQL/MetricsQL query over a time period. Returns time series data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "PromQL or MetricsQL expression"},
                    "start": {"type": "string", "description": "Start timestamp (ISO 8601 or epoch ms)"},
                    "end": {"type": "string", "description": "End timestamp (ISO 8601 or epoch ms). Defaults to now."},
                    "step": {"type": "string", "description": "Query resolution step (e.g. '1m', '5m', '1h')"},
                },
                "required": ["query", "start"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "metrics",
            "description": "List available metric names in VictoriaMetrics. Optionally filter by pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "match": {"type": "string", "description": "Optional series selector to filter metrics"},
                    "limit": {"type": "integer", "description": "Max number of metrics to return"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "labels",
            "description": "List available label names in VictoriaMetrics.",
            "parameters": {
                "type": "object",
                "properties": {
                    "match": {"type": "string", "description": "Optional series selector to filter labels"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "label_values",
            "description": "List values for a specific label in VictoriaMetrics.",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Label name to get values for (e.g. 'job', 'instance', '__name__')",
                    },
                    "match": {"type": "string", "description": "Optional series selector to filter"},
                },
                "required": ["label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "alerts",
            "description": "View current alerts (firing and pending) from VictoriaMetrics/vmalert.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tsdb_status",
            "description": "View TSDB cardinality statistics from VictoriaMetrics. Shows top series, labels, and label values by cardinality.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topN": {"type": "integer", "description": "Number of top entries to return (default: 10)"},
                    "date": {"type": "string", "description": "Date for cardinality stats (YYYY-MM-DD). Defaults to today."},
                },
            },
        },
    },
]
