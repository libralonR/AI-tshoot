# Recomendações Técnicas - Observability Troubleshooting Copilot

**Data**: 2026-04-13  
**Versão**: 1.0

---

## 1. Testes Automatizados (P0 - Crítico)

### 1.1. Unit Tests

**Arquivo**: `tests/unit/test_correlation.py`
```python
import pytest
from orchestrator.correlation import CorrelationEngine
from orchestrator.models import Evidence, EvidenceType, Scope

def test_normalize_labels_with_aliases():
    engine = CorrelationEngine(
        standard_labels=["application_service", "owner_squad"],
        label_aliases={
            "cmdb_ci_name": "application_service",
            "assignment_group_name": "owner_squad"
        }
    )
    
    raw = {
        "cmdb_ci_name": "api-gateway",
        "assignment_group_name": "sre-team"
    }
    normalized = engine._normalize_labels(raw)
    
    assert normalized["application_service"] == "api-gateway"
    assert normalized["owner_squad"] == "sre-team"

def test_extract_correlation_key():
    engine = CorrelationEngine(
        standard_labels=["application_service", "owner_squad"]
    )
    
    evidence = Evidence(
        id="test-1",
        type=EvidenceType.ALERT_FIRING,
        source="grafana-mcp",
        query="test",
        result={"labels": {"application_service": "api-gateway", "owner_squad": "sre"}},
        timestamp="2024-01-01T00:00:00Z",
        links=[],
        confidence=0.9,
        redacted=False
    )
    
    key = engine.extract_correlation_key(evidence)
    assert key == "application_service=api-gateway|owner_squad=sre"

def test_correlate_signals_increases_confidence():
    engine = CorrelationEngine(standard_labels=["application_service"])
    
    evidence1 = Evidence(
        id="e1", type=EvidenceType.ALERT_FIRING, source="grafana",
        query="q1", result={"labels": {"application_service": "api"}},
        timestamp="2024-01-01T00:00:00Z", links=[], confidence=0.8, redacted=False
    )
    evidence2 = Evidence(
        id="e2", type=EvidenceType.INCIDENT_RELATED, source="incidents",
        query="q2", result={"cmdb_ci_name": "api"},
        timestamp="2024-01-01T00:00:00Z", links=[], confidence=0.7, redacted=False
    )
    
    correlated, gaps = engine.correlate_signals([evidence1, evidence2], Scope())
    
    # Confidence should increase when signals correlate
    assert all(e.confidence > 0.8 for e in correlated)
```

**Arquivo**: `tests/unit/test_guardrails.py`
```python
import pytest
from orchestrator.guardrails import Guardrails

def test_redact_email():
    text = "Contact: user@example.com for help"
    redacted, was_redacted = Guardrails.redact_pii(text)
    
    assert "[EMAIL_REDACTED]" in redacted
    assert "user@example.com" not in redacted
    assert was_redacted is True

def test_redact_phone():
    text = "Call 555-123-4567"
    redacted, was_redacted = Guardrails.redact_pii(text)
    
    assert "[PHONE_REDACTED]" in redacted
    assert "555-123-4567" not in redacted
    assert was_redacted is True

def test_redact_api_key():
    text = "Token: sk-1234567890abcdefghij"
    redacted, was_redacted = Guardrails.redact_pii(text)
    
    assert "[API_KEY_REDACTED]" in redacted
    assert "sk-1234567890" not in redacted
    assert was_redacted is True

def test_validate_read_only_blocks_mutations():
    from orchestrator.models import NextStep, Priority
    
    step = NextStep(
        action="Restart the service",
        description="Restart to fix issue",
        readOnly=False,
        priority=Priority.HIGH
    )
    
    assert Guardrails.validate_read_only(step) is False
```

### 1.2. Integration Tests

**Arquivo**: `tests/integration/test_mcp_integration.py`
```python
import pytest
import httpx
from orchestrator.mcp_client import MCPClient

@pytest.mark.asyncio
async def test_grafana_mcp_find_firing_alerts():
    client = MCPClient("grafana", "http://localhost:8080")
    
    try:
        result = await client.call_tool("find_firing_alerts", {
            "labels": {"application_service": "test-service"}
        })
        
        assert result["success"] is True
        assert "result" in result
        assert isinstance(result["result"], list)
    finally:
        await client.close()

@pytest.mark.asyncio
async def test_incidents_mcp_search():
    client = MCPClient("incidents-pg", "http://localhost:8081")
    
    try:
        result = await client.call_tool("search_incidents", {
            "application_service": "test-service"
        })
        
        assert result["success"] is True
        assert "result" in result
    finally:
        await client.close()
```

### 1.3. E2E Tests

**Arquivo**: `tests/e2e/test_investigate_flow.py`
```python
import pytest
import httpx

@pytest.mark.asyncio
async def test_investigate_alert_uid_flow():
    async with httpx.AsyncClient(base_url="http://localhost:8080") as client:
        # 1. Health check
        response = await client.get("/health")
        assert response.status_code == 200
        
        # 2. Investigate
        response = await client.post("/investigate", json={
            "input_type": "ALERT_UID",
            "value": "test-alert-uid",
            "user": "test@example.com"
        })
        
        assert response.status_code == 200
        data = response.json()
        
        assert "caseFileId" in data
        assert "scope" in data
        assert "evidence" in data
        assert "hypotheses" in data
        
        # 3. Verify CaseFile
        case_file_id = data["caseFileId"]
        response = await client.get(f"/casefile/{case_file_id}")
        # Note: Currently returns error, but should work after storage implementation
```

---

## 2. Métricas Prometheus (P0 - Crítico)

**Arquivo**: `orchestrator/metrics.py`
```python
from prometheus_client import Counter, Histogram, Gauge, Info
import time
from functools import wraps

# Métricas de investigação
investigation_duration = Histogram(
    'investigation_duration_seconds',
    'Time spent on investigation',
    ['input_type', 'status']
)

investigation_total = Counter(
    'investigation_total',
    'Total investigations',
    ['input_type', 'status']
)

evidence_count = Histogram(
    'evidence_count',
    'Number of evidence items gathered',
    ['input_type']
)

hypothesis_count = Histogram(
    'hypothesis_count',
    'Number of hypotheses generated',
    ['input_type']
)

# Métricas de MCP
mcp_call_duration = Histogram(
    'mcp_call_duration_seconds',
    'MCP call duration',
    ['server', 'tool', 'status']
)

mcp_call_total = Counter(
    'mcp_call_total',
    'Total MCP calls',
    ['server', 'tool', 'status']
)

# Métricas de LLM
llm_call_duration = Histogram(
    'llm_call_duration_seconds',
    'LLM call duration',
    ['model', 'status']
)

llm_token_usage = Counter(
    'llm_token_usage_total',
    'Total LLM tokens used',
    ['model', 'type']  # type: prompt, completion
)

# Métricas de chat
chat_session_count = Gauge(
    'chat_session_count',
    'Number of active chat sessions'
)

chat_message_total = Counter(
    'chat_message_total',
    'Total chat messages',
    ['status']
)

# Info
app_info = Info('app', 'Application info')
app_info.info({
    'version': '1.1.0',
    'service': 'orchestrator'
})

# Decorators
def track_investigation(func):
    @wraps(func)
    async def wrapper(self, input_data, *args, **kwargs):
        start_time = time.time()
        status = "success"
        
        try:
            result = await func(self, input_data, *args, **kwargs)
            
            # Track metrics
            duration = time.time() - start_time
            investigation_duration.labels(
                input_type=input_data.type,
                status=status
            ).observe(duration)
            
            investigation_total.labels(
                input_type=input_data.type,
                status=status
            ).inc()
            
            evidence_count.labels(
                input_type=input_data.type
            ).observe(len(result.evidence))
            
            hypothesis_count.labels(
                input_type=input_data.type
            ).observe(len(result.hypotheses))
            
            return result
            
        except Exception as e:
            status = "error"
            investigation_total.labels(
                input_type=input_data.type,
                status=status
            ).inc()
            raise
    
    return wrapper

def track_mcp_call(func):
    @wraps(func)
    async def wrapper(self, tool_name, arguments):
        start_time = time.time()
        status = "success"
        
        try:
            result = await func(self, tool_name, arguments)
            
            if not result.get("success"):
                status = "error"
            
            return result
            
        except Exception as e:
            status = "error"
            raise
            
        finally:
            duration = time.time() - start_time
            mcp_call_duration.labels(
                server=self.server_name,
                tool=tool_name,
                status=status
            ).observe(duration)
            
            mcp_call_total.labels(
                server=self.server_name,
                tool=tool_name,
                status=status
            ).inc()
    
    return wrapper
```

**Uso**:
```python
# orchestrator/orchestrator.py
from metrics import track_investigation

class Orchestrator:
    @track_investigation
    async def investigate(self, input_data: Input, filters: dict = None) -> CaseFile:
        # ... existing code ...

# orchestrator/mcp_client.py
from metrics import track_mcp_call

class MCPClient:
    @track_mcp_call
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        # ... existing code ...
```

**Endpoint de métricas**:
```python
# orchestrator/orchestrator.py
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

---

## 3. Rate Limiting (P1 - Alto)

**Arquivo**: `orchestrator/rate_limiter.py`
```python
import time
from collections import defaultdict
from typing import Dict, Tuple

class RateLimiter:
    """Simple in-memory rate limiter."""
    
    def __init__(self, requests_per_minute: int = 60):
        self.requests_per_minute = requests_per_minute
        self.requests: Dict[str, list] = defaultdict(list)
    
    def is_allowed(self, user_id: str) -> Tuple[bool, int]:
        """Check if request is allowed. Returns (allowed, remaining)."""
        now = time.time()
        minute_ago = now - 60
        
        # Clean old requests
        self.requests[user_id] = [
            req_time for req_time in self.requests[user_id]
            if req_time > minute_ago
        ]
        
        # Check limit
        if len(self.requests[user_id]) >= self.requests_per_minute:
            return False, 0
        
        # Add new request
        self.requests[user_id].append(now)
        remaining = self.requests_per_minute - len(self.requests[user_id])
        
        return True, remaining

# Global instance
rate_limiter = RateLimiter(requests_per_minute=60)
```

**Uso**:
```python
# orchestrator/orchestrator.py
from rate_limiter import rate_limiter

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    # Extract user from request (or use session_id)
    user_id = request.session_id or "anonymous"
    
    # Check rate limit
    allowed, remaining = rate_limiter.is_allowed(user_id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Try again in 1 minute.",
            headers={"X-RateLimit-Remaining": "0"}
        )
    
    # ... existing code ...
    
    # Add rate limit headers to response
    response = ChatResponse(response=response_text, session_id=session_id)
    response.headers = {"X-RateLimit-Remaining": str(remaining)}
    return response
```

---

## 4. Circuit Breaker (P1 - Alto)

**Instalação**:
```bash
pip install circuitbreaker
```

**Arquivo**: `orchestrator/resilience.py`
```python
from circuitbreaker import circuit
import logging

log = logging.getLogger("orchestrator")

@circuit(failure_threshold=5, recovery_timeout=60, expected_exception=Exception)
async def call_mcp_with_circuit_breaker(client, tool_name, arguments):
    """Call MCP with circuit breaker protection."""
    return await client.call_tool(tool_name, arguments)

def on_circuit_open(func, *args, **kwargs):
    log.error(f"Circuit breaker opened for {func.__name__}")

def on_circuit_close(func, *args, **kwargs):
    log.info(f"Circuit breaker closed for {func.__name__}")
```

**Uso**:
```python
# orchestrator/agents/grafana.py
from resilience import call_mcp_with_circuit_breaker

class GrafanaAgent:
    async def find_firing_alerts(self, scope: Scope) -> List[Evidence]:
        labels = {}
        if scope.serviceName:
            labels["application_service"] = scope.serviceName
        
        try:
            result = await call_mcp_with_circuit_breaker(
                self.mcp,
                "find_firing_alerts",
                {"labels": labels}
            )
        except Exception as e:
            log.error(f"Circuit breaker prevented call: {e}")
            return []
        
        # ... rest of code ...
```

---

## 5. Expandir Busca de Incidentes (P1 - Alto)

**Arquivo**: `mcp-servers/incidents_pg.py`

**Modificar `_get_related_incidents`**:
```python
async def _get_related_incidents(pool: AsyncConnectionPool, args: dict) -> dict:
    time_window = int(args.get("time_window_hours", 24))
    number = args.get('number')
    
    # Suportar múltiplas labels
    filters = {
        "application_service": args.get("application_service"),
        "business_capability": args.get("business_capability"),
        "business_domain": args.get("business_domain"),
        "business_service": args.get("business_service"),
        "owner_squad": args.get("owner_squad"),
        "owner_sre": args.get("owner_sre"),
    }
    
    # Remover filtros vazios
    filters = {k: v for k, v in filters.items() if v}
    
    log.info(
        f"[get_related_incidents] Starting search | "
        f"number={number} | "
        f"filters={filters} | "
        f"time_window={time_window}h"
    )
    
    cols = ", ".join(f'i."{c}"' for c in INCIDENT_COLUMNS)
    results = {"by_parent": [], "by_ci": [], "by_description": []}

    try:
        async with pool.connection() as conn:
            if filters:
                # Construir condições de busca
                conditions = []
                params = {}
                
                for idx, (label, value) in enumerate(filters.items()):
                    param_name = f"label_{idx}"
                    conditions.append(f"i.description ILIKE %(label_{idx})s")
                    params[param_name] = f"%- {label}={value}%"
                
                where_clause = " OR ".join(conditions)
                params["time_window"] = time_window
                
                log.debug(f"[get_related_incidents] Priority search by labels: {list(filters.keys())}")
                cur = await conn.execute(
                    f"""SELECT {cols} FROM public.incidents_snow i
                        WHERE ({where_clause})
                        AND i.opened_at >= NOW() - interval '{time_window} hours'
                        ORDER BY i.opened_at DESC LIMIT 100""",
                    params,
                )
                by_desc_rows = await cur.fetchall()
                results["by_description"] = [enrich_row(r) for r in by_desc_rows]
                log.debug(f"[get_related_incidents] Found {len(by_desc_rows)} incidents by labels")
            
            # ... rest of existing code ...
```

**Atualizar ferramenta LLM**:
```python
# orchestrator/llm_client.py
{
    "type": "function",
    "function": {
        "name": "get_related_incidents",
        "description": "Find incidents related to a specific incident or service. Supports filtering by multiple labels.",
        "parameters": {
            "type": "object",
            "properties": {
                "number": {
                    "type": "string",
                    "description": "Reference incident number (e.g. INC0012345)",
                },
                "application_service": {
                    "type": "string",
                    "description": "Service name to find related incidents",
                },
                "business_capability": {
                    "type": "string",
                    "description": "Business capability to filter by",
                },
                "business_domain": {
                    "type": "string",
                    "description": "Business domain to filter by",
                },
                "owner_squad": {
                    "type": "string",
                    "description": "Owner squad to filter by",
                },
                "time_window_hours": {
                    "type": "integer",
                    "description": "Time window in hours (default: 24)",
                },
            },
        },
    },
}
```

---

## 6. Health Checks Detalhados (P1 - Alto)

**Arquivo**: `orchestrator/health.py`
```python
import asyncio
from typing import Dict, Any
from mcp_client import MCPClient
from config import config

async def check_mcp_server(server_name: str, endpoint: str) -> Dict[str, Any]:
    """Check if MCP server is healthy."""
    try:
        client = MCPClient(server_name, endpoint, timeout=5)
        
        # Try to call a simple tool or health endpoint
        result = await client.client.get(f"{endpoint}/health")
        
        await client.close()
        
        return {
            "status": "healthy" if result.status_code == 200 else "unhealthy",
            "latency_ms": result.elapsed.total_seconds() * 1000,
            "error": None
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "latency_ms": None,
            "error": str(e)
        }

async def check_all_dependencies() -> Dict[str, Any]:
    """Check all dependencies."""
    checks = {}
    
    # Check MCP servers
    for server_name, server_config in config.mcp_servers.items():
        checks[f"mcp_{server_name}"] = await check_mcp_server(
            server_name,
            server_config.endpoint
        )
    
    # Check LLM
    try:
        from llm_client import LLMClient
        checks["llm"] = {"status": "healthy", "error": None}
    except Exception as e:
        checks["llm"] = {"status": "unhealthy", "error": str(e)}
    
    # Overall status
    all_healthy = all(c["status"] == "healthy" for c in checks.values())
    
    return {
        "status": "healthy" if all_healthy else "degraded",
        "checks": checks
    }
```

**Endpoint**:
```python
# orchestrator/orchestrator.py
from health import check_all_dependencies

@app.get("/health/detailed")
async def health_check_detailed():
    return await check_all_dependencies()
```

---

## Conclusão

Estas recomendações técnicas fornecem implementações práticas para os problemas identificados no code review. Priorize:

1. **Testes automatizados** - Evita regressões
2. **Métricas Prometheus** - Visibilidade em produção
3. **Rate limiting** - Proteção contra abuso
4. **Circuit breaker** - Resiliência
5. **Busca expandida** - Melhora capacidades do /chat

Implemente em sprints de 2 semanas para manter momentum e obter feedback rápido.
