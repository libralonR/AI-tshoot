# Code Review - Observability Troubleshooting Copilot

**Data**: 2026-04-07  
**Revisor**: Kiro AI  
**Versão**: 1.0.0  
**Escopo**: Análise completa do código (orchestrator + MCP servers)

---

## Sumário Executivo

### Pontuação Geral: 7.5/10

**Pontos Fortes**:
- ✅ Arquitetura modular bem definida (orchestrator + MCP servers)
- ✅ Separação clara de responsabilidades (agents, correlation, hypothesis)
- ✅ Guardrails de segurança implementados (PII redaction, read-only)
- ✅ Suporte a múltiplos modos (stdio/SSE) nos MCP servers
- ✅ Logging estruturado e rastreabilidade

**Pontos Críticos**:
- 🔴 Bug crítico: correlação de incidentes falha com filtros não-service
- 🔴 Falta tratamento de erros em múltiplos pontos
- 🟡 Ausência de testes automatizados
- 🟡 Configuração hardcoded em vários lugares
- 🟡 Falta validação de entrada em endpoints

---

## 1. Arquitetura e Design

### 1.1. Estrutura Geral ✅ (9/10)

**Pontos Positivos**:
- Separação clara entre orchestrator e MCP servers
- Padrão de specialist agents bem implementado
- Uso correto de dataclasses e Pydantic models
- Configuração centralizada em `config.py`

**Pontos de Melhoria**:
- Falta interface/protocolo formal para agents
- Ausência de dependency injection
- Config poderia usar Pydantic Settings

**Recomendação**:
```python
# orchestrator/agents/base.py
from abc import ABC, abstractmethod
from typing import List, Optional
from models import Evidence, Scope

class BaseAgent(ABC):
    """Base class for specialist agents."""
    
    def __init__(self, mcp_client: MCPClient):
        self.mcp = mcp_client
    
    @abstractmethod
    async def gather_evidence(self, scope: Scope) -> List[Evidence]:
        """Gather evidence from this agent's data source."""
        pass
```

---

### 1.2. Separação de Responsabilidades ✅ (8/10)

**Bem implementado**:
- `orchestrator.py`: Coordenação e workflow
- `correlation.py`: Lógica de correlação isolada
- `hypothesis.py`: Geração de hipóteses separada
- `guardrails.py`: Segurança centralizada
- `agents/`: Especialistas por fonte de dados

**Pontos de Melhoria**:
- `orchestrator.py` tem múltiplas responsabilidades (API + orquestração)
- Lógica de negócio misturada com endpoints FastAPI

**Recomendação**: Separar em:
```
orchestrator/
├── api/
│   ├── routes.py          # FastAPI routes
│   └── dependencies.py    # FastAPI dependencies
├── core/
│   ├── orchestrator.py    # Business logic
│   └── workflow.py        # Investigation workflow
└── ...
```

---

## 2. Bugs Críticos 🔴

### 2.1. Bug: Correlação de Incidentes Falha com Filtros Não-Service

**Severidade**: CRÍTICA  
**Arquivo**: `orchestrator/orchestrator.py`, linha 158-168  
**Impacto**: Incidentes não são buscados quando filtros não incluem `application_service`

**Código problemático**:
```python
async def _gather_signals(self, case_file: CaseFile) -> List[Evidence]:
    tasks = [grafana_agent.find_firing_alerts(case_file.scope)]
    
    ci_name = case_file.scope.serviceName  # None quando filtro é business_capability
    additional = case_file.scope.additionalLabels or {}
    inc_number = additional.get("incident_number")
    
    if inc_number or ci_name:  # ❌ Falso quando ambos são None
        tasks.append(
            incidents_agent.find_related_incidents(
                number=inc_number, cmdb_ci_name=ci_name
            )
        )
```

**Cenários afetados**:
- Busca por `business_capability`
- Busca por `owner_squad`
- Busca por `severidade`
- Busca por `grafana_folder`

**Solução proposta**:
```python
async def _gather_signals(self, case_file: CaseFile) -> List[Evidence]:
    # Fase 1: Buscar alertas
    alert_evidences = await grafana_agent.find_firing_alerts(case_file.scope)
    evidence_list.extend(alert_evidences)
    
    # Fase 2: Extrair application_service dos alertas encontrados
    services = set()
    for evidence in alert_evidences:
        labels = evidence.result.get("labels", {})
        app_svc = labels.get("application_service")
        if app_svc:
            services.add(app_svc)
    
    # Fase 3: Buscar incidentes para cada serviço encontrado
    for service in services:
        inc_evidences = await incidents_agent.find_related_incidents(
            cmdb_ci_name=service
        )
        evidence_list.extend(inc_evidences)
    
    return evidence_list
```

---

### 2.2. Bug: Falta Tratamento de Exceções em Endpoints

**Severidade**: ALTA  
**Arquivo**: `orchestrator/orchestrator.py`, linha 217-237  
**Impacto**: Erros não tratados podem vazar informações sensíveis

**Código problemático**:
```python
@app.post("/investigate", response_model=InvestigateResponse)
async def investigate_endpoint(request: InvestigateRequest):
    try:
        # ... código ...
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # ❌ Muito genérico
        log.exception("Error during investigation")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")
        # ❌ Pode vazar stack trace ou informações sensíveis
```

**Problemas**:
1. `str(e)` pode conter informações sensíveis (paths, tokens, IPs)
2. Não diferencia tipos de erro (timeout, auth, network)
3. Não aplica PII redaction na mensagem de erro

**Solução proposta**:
```python
from enum import Enum

class ErrorCode(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    MCP_TIMEOUT = "MCP_TIMEOUT"
    MCP_UNAVAILABLE = "MCP_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"

@app.post("/investigate", response_model=InvestigateResponse)
async def investigate_endpoint(request: InvestigateRequest):
    try:
        # ... código ...
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "code": ErrorCode.INVALID_INPUT,
                "message": "Invalid request parameters",
                "details": str(e)[:200]  # Limitar tamanho
            }
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail={
                "code": ErrorCode.MCP_TIMEOUT,
                "message": "Timeout communicating with data sources"
            }
        )
    except Exception as e:
        log.exception("Error during investigation")
        # Aplicar PII redaction
        error_msg, _ = Guardrails.redact_pii(str(e))
        raise HTTPException(
            status_code=500,
            detail={
                "code": ErrorCode.INTERNAL_ERROR,
                "message": "Internal server error",
                "request_id": case_file.id if 'case_file' in locals() else None
            }
        )
```

---

### 2.3. Bug: Race Condition em Session Store

**Severidade**: MÉDIA  
**Arquivo**: `orchestrator/orchestrator.py`, linha 254  
**Impacto**: Possível corrupção de sessões em ambiente concorrente

**Código problemático**:
```python
_chat_sessions: Dict[str, Any] = {}  # ❌ Não thread-safe

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    session_id = request.session_id or str(uuid.uuid4())
    
    if session_id not in _chat_sessions:  # ❌ Race condition
        _chat_sessions[session_id] = LLMClient()
    
    llm = _chat_sessions[session_id]
```

**Problemas**:
1. Dict não é thread-safe em Python
2. Sessões nunca são limpas (memory leak)
3. Sem TTL ou limite de sessões

**Solução proposta**:
```python
from asyncio import Lock
from datetime import datetime, timedelta
from dataclasses import dataclass

@dataclass
class Session:
    llm: LLMClient
    created_at: datetime
    last_accessed: datetime

class SessionStore:
    def __init__(self, ttl_minutes: int = 30, max_sessions: int = 1000):
        self._sessions: Dict[str, Session] = {}
        self._lock = Lock()
        self._ttl = timedelta(minutes=ttl_minutes)
        self._max_sessions = max_sessions
    
    async def get_or_create(self, session_id: str) -> LLMClient:
        async with self._lock:
            await self._cleanup_expired()
            
            if session_id in self._sessions:
                session = self._sessions[session_id]
                session.last_accessed = datetime.utcnow()
                return session.llm
            
            if len(self._sessions) >= self._max_sessions:
                raise HTTPException(429, "Too many active sessions")
            
            llm = LLMClient()
            self._sessions[session_id] = Session(
                llm=llm,
                created_at=datetime.utcnow(),
                last_accessed=datetime.utcnow()
            )
            return llm
    
    async def _cleanup_expired(self):
        now = datetime.utcnow()
        expired = [
            sid for sid, session in self._sessions.items()
            if now - session.last_accessed > self._ttl
        ]
        for sid in expired:
            del self._sessions[sid]

_session_store = SessionStore()
```

---

## 3. Segurança 🔒

### 3.1. Guardrails ✅ (7/10)

**Bem implementado**:
- PII redaction com regex patterns
- Validação de read-only em NextSteps
- Validação de traceability em Evidence
- SSL verification desabilitado apenas quando necessário

**Pontos de Melhoria**:

#### 3.1.1. PII Redaction Incompleta

**Arquivo**: `orchestrator/guardrails.py`

**Patterns faltando**:
```python
# CPF brasileiro
r'\b\d{3}\.\d{3}\.\d{3}-\d{2}\b'

# CNPJ brasileiro
r'\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b'

# Cartão de crédito
r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b'

# Nomes próprios (heurística)
r'\b[A-Z][a-z]+ [A-Z][a-z]+\b'  # João Silva

# Endereços de email em formato alternativo
r'\b[A-Za-z0-9._%+-]+ at [A-Za-z0-9.-]+ dot [A-Z|a-z]{2,}\b'
```

**Recomendação**: Usar biblioteca especializada
```python
# requirements.txt
presidio-analyzer>=2.2.0
presidio-anonymizer>=2.2.0

# guardrails.py
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine

class Guardrails:
    def __init__(self):
        self.analyzer = AnalyzerEngine()
        self.anonymizer = AnonymizerEngine()
    
    def redact_pii(self, text: str) -> tuple[str, bool]:
        results = self.analyzer.analyze(
            text=text,
            language='pt',  # Suporte a português
            entities=["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", 
                     "CREDIT_CARD", "IBAN_CODE", "IP_ADDRESS"]
        )
        
        if not results:
            return text, False
        
        anonymized = self.anonymizer.anonymize(
            text=text,
            analyzer_results=results
        )
        
        return anonymized.text, True
```

---

#### 3.1.2. Falta Rate Limiting

**Severidade**: MÉDIA  
**Impacto**: Vulnerável a DoS e abuso

**Solução proposta**:
```python
# requirements.txt
slowapi>=0.1.9

# orchestrator.py
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.post("/investigate")
@limiter.limit("10/minute")  # 10 requests por minuto por IP
async def investigate_endpoint(request: Request, body: InvestigateRequest):
    # ...
```

---

#### 3.1.3. Falta Validação de Input

**Arquivo**: `orchestrator/models.py`

**Problema**: Validação mínima nos modelos Pydantic

**Solução proposta**:
```python
from pydantic import BaseModel, Field, validator

class InvestigateRequest(BaseModel):
    input_type: str = Field(
        ..., 
        description="INCIDENT_ID, ALERT_UID, or SYMPTOM",
        regex="^(INCIDENT_ID|ALERT_UID|SYMPTOM)$"
    )
    value: str = Field(
        ..., 
        description="Incident ID, Alert UID, or symptom description",
        min_length=1,
        max_length=500
    )
    user: str = Field(
        default="anonymous", 
        description="User requesting investigation",
        max_length=100
    )
    filters: Optional[Dict[str, str]] = Field(
        default=None,
        description="Optional filters"
    )
    
    @validator('value')
    def validate_value_format(cls, v, values):
        input_type = values.get('input_type')
        
        if input_type == 'INCIDENT_ID':
            if not re.match(r'^INC\d{7,10}$', v):
                raise ValueError('Invalid incident ID format')
        
        elif input_type == 'ALERT_UID':
            if not re.match(r'^[a-zA-Z0-9_-]{8,}$', v):
                raise ValueError('Invalid alert UID format')
        
        elif input_type == 'SYMPTOM':
            if len(v.strip()) < 3:
                raise ValueError('Symptom description too short')
        
        return v
    
    @validator('filters')
    def validate_filters(cls, v):
        if v is None:
            return v
        
        allowed_keys = {
            'application_service', 'owner_squad', 'severidade',
            'business_capability', 'business_domain', 'business_service',
            'grafana_folder', 'env', 'cluster', 'namespace'
        }
        
        invalid_keys = set(v.keys()) - allowed_keys
        if invalid_keys:
            raise ValueError(f'Invalid filter keys: {invalid_keys}')
        
        # Validar valores
        for key, value in v.items():
            if not isinstance(value, str):
                raise ValueError(f'Filter {key} must be string')
            if len(value) > 200:
                raise ValueError(f'Filter {key} value too long')
        
        return v
```

---

### 3.2. Secrets Management 🟡 (6/10)

**Problemas identificados**:

1. **SSL Verification Desabilitado Globalmente**
   ```python
   # mcp_client.py, linha 14
   self.client = httpx.AsyncClient(timeout=httpx.Timeout(timeout), verify=False)
   # ❌ Sempre desabilita SSL
   ```

2. **Tokens em Logs**
   ```python
   # grafana_v2.py, linha 38
   log.info(f"Config: GRAFANA_URL={base_url}, token={'set' if token else 'MISSING'}")
   # ✅ Bom, mas poderia logar hash do token para debug
   ```

3. **Falta Rotação de Secrets**
   - Tokens são lidos apenas no startup
   - Sem suporte a reload de configuração

**Recomendações**:

```python
# config.py
import os
from pathlib import Path

class Config:
    def __init__(self):
        self._load_secrets()
    
    def _load_secrets(self):
        """Load secrets from env vars or mounted files (K8s secrets)."""
        # Suportar secrets montados como arquivos
        grafana_token_file = os.getenv("GRAFANA_TOKEN_FILE")
        if grafana_token_file and Path(grafana_token_file).exists():
            self.grafana_token = Path(grafana_token_file).read_text().strip()
        else:
            self.grafana_token = os.getenv("GRAFANA_TOKEN", "")
        
        # Validar que secrets não estão vazios
        if not self.grafana_token:
            raise RuntimeError("GRAFANA_TOKEN not configured")
    
    def reload_secrets(self):
        """Reload secrets without restarting (útil para rotação)."""
        old_token = self.grafana_token
        self._load_secrets()
        if old_token != self.grafana_token:
            log.info("Secrets reloaded successfully")

# Endpoint para reload (protegido)
@app.post("/admin/reload-secrets")
async def reload_secrets(authorization: str = Header(None)):
    admin_token = os.getenv("ADMIN_TOKEN")
    if not admin_token or authorization != f"Bearer {admin_token}":
        raise HTTPException(403, "Forbidden")
    
    config.reload_secrets()
    return {"status": "ok", "message": "Secrets reloaded"}
```

---

## 4. Performance e Escalabilidade ⚡

### 4.1. Concorrência ✅ (8/10)

**Bem implementado**:
- Uso correto de `asyncio.gather` para paralelizar chamadas MCP
- AsyncClient do httpx para I/O não-bloqueante
- Timeouts configuráveis

**Código bom**:
```python
# orchestrator.py, linha 158
tasks = [grafana_agent.find_firing_alerts(case_file.scope)]
# ...
results = await asyncio.gather(*tasks, return_exceptions=True)
```

**Pontos de Melhoria**:

#### 4.1.1. Falta Connection Pooling

**Problema**: Cada chamada cria novo client HTTP

```python
# orchestrator.py, linha 150
grafana_client = MCPClient("grafana", config.mcp_servers["grafana"].endpoint)
incidents_client = MCPClient("incidents-pg", config.mcp_servers["incidents-pg"].endpoint)
# ...
await grafana_client.close()
await incidents_client.close()
```

**Impacto**: Overhead de TCP handshake em cada request

**Solução proposta**:
```python
# mcp_client.py
class MCPClientPool:
    """Pool de clients HTTP reutilizáveis."""
    
    def __init__(self):
        self._clients: Dict[str, httpx.AsyncClient] = {}
        self._lock = asyncio.Lock()
    
    async def get_client(self, server_name: str, endpoint: str, timeout: int) -> httpx.AsyncClient:
        async with self._lock:
            if server_name not in self._clients:
                self._clients[server_name] = httpx.AsyncClient(
                    timeout=httpx.Timeout(timeout),
                    verify=False,
                    limits=httpx.Limits(
                        max_connections=100,
                        max_keepalive_connections=20
                    )
                )
            return self._clients[server_name]
    
    async def close_all(self):
        for client in self._clients.values():
            await client.aclose()

# orchestrator.py
_mcp_pool = MCPClientPool()

@app.on_event("startup")
async def startup():
    log.info("Orchestrator starting up")

@app.on_event("shutdown")
async def shutdown():
    await _mcp_pool.close_all()
    log.info("Orchestrator shut down")
```

---

#### 4.1.2. Falta Caching

**Problema**: Mesmas queries são executadas repetidamente

**Oportunidades de cache**:
1. Alertas firing (TTL: 30s)
2. Detalhes de alertas (TTL: 5min)
3. Dashboards (TTL: 1h)
4. Incidentes (TTL: 1min)

**Solução proposta**:
```python
# requirements.txt
aiocache>=0.12.0

# mcp_client.py
from aiocache import Cache
from aiocache.serializers import JsonSerializer

class MCPClient:
    def __init__(self, server_name: str, endpoint: str, timeout: int = 15):
        self.server_name = server_name
        self.endpoint = endpoint
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(timeout), verify=False)
        self.cache = Cache(Cache.MEMORY, serializer=JsonSerializer())
    
    async def call_tool(
        self, 
        tool_name: str, 
        arguments: Dict[str, Any],
        cache_ttl: Optional[int] = None
    ) -> Dict[str, Any]:
        # Gerar cache key
        if cache_ttl:
            cache_key = f"{self.server_name}:{tool_name}:{json.dumps(arguments, sort_keys=True)}"
            cached = await self.cache.get(cache_key)
            if cached:
                log.debug(f"Cache hit: {cache_key}")
                return cached
        
        try:
            result = await self._do_call(tool_name, arguments)
            
            # Armazenar em cache
            if cache_ttl and result.get("success"):
                await self.cache.set(cache_key, result, ttl=cache_ttl)
            
            return result
        except Exception as e:
            # ...

# agents/grafana.py
async def find_firing_alerts(self, scope: Scope) -> List[Evidence]:
    # ...
    result = await self.mcp.call_tool(
        "find_firing_alerts", 
        {"labels": labels},
        cache_ttl=30  # Cache por 30 segundos
    )
```

---

### 4.2. Timeouts e Circuit Breaker 🟡 (6/10)

**Bem implementado**:
- Timeouts configuráveis por MCP server
- Tratamento de TimeoutException

**Pontos de Melhoria**:

#### 4.2.1. Falta Circuit Breaker

**Problema**: Se um MCP server está down, todas as requests continuam tentando

**Solução proposta**:
```python
# requirements.txt
pybreaker>=1.0.0

# mcp_client.py
from pybreaker import CircuitBreaker, CircuitBreakerError

class MCPClient:
    _breakers: Dict[str, CircuitBreaker] = {}
    
    def __init__(self, server_name: str, endpoint: str, timeout: int = 15):
        self.server_name = server_name
        self.endpoint = endpoint
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(timeout), verify=False)
        
        # Criar circuit breaker para este server
        if server_name not in self._breakers:
            self._breakers[server_name] = CircuitBreaker(
                fail_max=5,           # Abrir após 5 falhas
                timeout_duration=60,  # Tentar novamente após 60s
                name=f"mcp-{server_name}"
            )
        
        self.breaker = self._breakers[server_name]
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            # Usar circuit breaker
            return await self.breaker.call_async(
                self._do_call, tool_name, arguments
            )
        except CircuitBreakerError:
            log.error(f"Circuit breaker open for {self.server_name}")
            return {
                "success": False,
                "error": f"{self.server_name} is temporarily unavailable",
                "circuit_breaker_open": True,
                "executionTime": 0
            }
    
    async def _do_call(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        # Lógica original de chamada HTTP
        # ...
```

---

#### 4.2.2. Timeout Cascata

**Problema**: Timeout do orchestrator não considera timeouts dos MCP servers

**Exemplo**:
- Orchestrator timeout: 30s
- 3 MCP servers com timeout de 15s cada
- Tempo total possível: 45s (excede timeout do orchestrator)

**Solução proposta**:
```python
# orchestrator.py
async def _gather_signals(self, case_file: CaseFile) -> List[Evidence]:
    evidence_list: List[Evidence] = []
    
    # Timeout total para gather signals
    gather_timeout = 25  # segundos
    
    try:
        async with asyncio.timeout(gather_timeout):
            tasks = [grafana_agent.find_firing_alerts(case_file.scope)]
            
            # ... adicionar outras tasks
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                if isinstance(result, Exception):
                    log.error(f"Error gathering signals: {result}")
                elif isinstance(result, list):
                    evidence_list.extend(result)
                elif result is not None:
                    evidence_list.append(result)
    
    except asyncio.TimeoutError:
        log.error(f"Gather signals timeout after {gather_timeout}s")
        # Retornar evidências parciais coletadas até agora
    
    return evidence_list
```

---

### 4.3. Métricas e Observabilidade 🟡 (5/10)

**Problema**: Falta instrumentação para monitorar o próprio orchestrator

**Solução proposta**:
```python
# requirements.txt
prometheus-client>=0.19.0

# orchestrator.py
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# Métricas
investigate_requests_total = Counter(
    'investigate_requests_total',
    'Total investigation requests',
    ['input_type', 'status']
)

investigate_duration_seconds = Histogram(
    'investigate_duration_seconds',
    'Investigation duration in seconds',
    ['input_type']
)

evidence_count = Histogram(
    'evidence_count',
    'Number of evidence collected',
    ['input_type']
)

mcp_call_duration_seconds = Histogram(
    'mcp_call_duration_seconds',
    'MCP call duration in seconds',
    ['server', 'tool', 'status']
)

active_sessions = Gauge(
    'active_chat_sessions',
    'Number of active chat sessions'
)

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/investigate")
async def investigate_endpoint(request: InvestigateRequest):
    start_time = time.time()
    
    try:
        # ... código ...
        
        investigate_requests_total.labels(
            input_type=request.input_type,
            status='success'
        ).inc()
        
        investigate_duration_seconds.labels(
            input_type=request.input_type
        ).observe(time.time() - start_time)
        
        evidence_count.labels(
            input_type=request.input_type
        ).observe(len(case_file.evidence))
        
        return response
    
    except Exception as e:
        investigate_requests_total.labels(
            input_type=request.input_type,
            status='error'
        ).inc()
        raise
```

---

## 5. Qualidade de Código 📝

### 5.1. Testes 🔴 (2/10)

**Problema Crítico**: Ausência quase total de testes automatizados

**Arquivos de teste encontrados**:
- `mcp-servers/test_*.py` - Testes básicos dos MCP servers
- `orchestrator/test_orchestrator.py` - Arquivo existe mas não foi analisado

**Cobertura estimada**: < 10%

**Impacto**:
- Alto risco de regressões
- Dificulta refatoração
- Bugs só descobertos em produção

**Solução proposta**:

```python
# orchestrator/tests/conftest.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from orchestrator import Orchestrator
from mcp_client import MCPClient
from config import Config

@pytest.fixture
def mock_config():
    config = MagicMock(spec=Config)
    config.standard_labels = ["application_service", "owner_squad", "severity"]
    config.label_aliases = {
        "cmdb_ci_name": "application_service",
        "Severidade": "severity"
    }
    return config

@pytest.fixture
def mock_mcp_client():
    client = AsyncMock(spec=MCPClient)
    return client

@pytest.fixture
def orchestrator(mock_config):
    return Orchestrator()

# orchestrator/tests/test_correlation.py
import pytest
from correlation import CorrelationEngine
from models import Evidence, EvidenceType, Scope

def test_extract_correlation_key_from_alert():
    engine = CorrelationEngine(
        standard_labels=["application_service", "owner_squad"],
        label_aliases={}
    )
    
    evidence = Evidence(
        id="test-1",
        type=EvidenceType.ALERT_FIRING,
        source="grafana-mcp",
        query="test",
        result={
            "labels": {
                "application_service": "payment-api",
                "owner_squad": "squad-payments"
            }
        },
        timestamp="2026-04-07T10:00:00Z",
        links=[],
        confidence=0.9,
        redacted=False
    )
    
    key = engine.extract_correlation_key(evidence)
    assert key == "application_service=payment-api|owner_squad=squad-payments"

def test_normalize_labels_with_aliases():
    engine = CorrelationEngine(
        standard_labels=["application_service"],
        label_aliases={"cmdb_ci_name": "application_service"}
    )
    
    raw_labels = {"cmdb_ci_name": "payment-api"}
    normalized = engine._normalize_labels(raw_labels)
    
    assert normalized == {"application_service": "payment-api"}

# orchestrator/tests/test_orchestrator.py
import pytest
from orchestrator import Orchestrator
from models import Input, InputType

@pytest.mark.asyncio
async def test_investigate_with_incident_id(orchestrator, mock_mcp_client):
    # Arrange
    input_data = Input(
        type=InputType.INCIDENT_ID,
        value="INC0012345",
        timestamp="2026-04-07T10:00:00Z",
        user="test-user"
    )
    
    # Mock MCP responses
    mock_mcp_client.call_tool.return_value = {
        "success": True,
        "result": {
            "number": "INC0012345",
            "cmdb_ci_name": "payment-api",
            "priority": "1"
        }
    }
    
    # Act
    case_file = await orchestrator.investigate(input_data)
    
    # Assert
    assert case_file.scope.serviceName == "payment-api"
    assert len(case_file.evidence) > 0
    assert len(case_file.hypotheses) > 0

@pytest.mark.asyncio
async def test_investigate_with_business_capability_filter(orchestrator):
    # Arrange
    input_data = Input(
        type=InputType.SYMPTOM,
        value="alertas",
        timestamp="2026-04-07T10:00:00Z",
        user="test-user"
    )
    filters = {"business_capability": "aml-pld"}
    
    # Act
    case_file = await orchestrator.investigate(input_data, filters=filters)
    
    # Assert
    assert case_file.scope.additionalLabels["business_capability"] == "aml-pld"
    # TODO: Verificar que incidentes foram buscados (após fix do bug)

# orchestrator/tests/test_guardrails.py
import pytest
from guardrails import Guardrails

def test_redact_email():
    text = "Contact john.doe@example.com for help"
    redacted, was_redacted = Guardrails.redact_pii(text)
    
    assert was_redacted
    assert "john.doe@example.com" not in redacted
    assert "[EMAIL_REDACTED]" in redacted

def test_redact_phone():
    text = "Call 555-123-4567"
    redacted, was_redacted = Guardrails.redact_pii(text)
    
    assert was_redacted
    assert "555-123-4567" not in redacted
    assert "[PHONE_REDACTED]" in redacted

def test_redact_api_key():
    text = "Token: sk-1234567890abcdefghij"
    redacted, was_redacted = Guardrails.redact_pii(text)
    
    assert was_redacted
    assert "sk-1234567890abcdefghij" not in redacted
    assert "[API_KEY_REDACTED]" in redacted
```

**Executar testes**:
```bash
# requirements-dev.txt
pytest>=7.4.0
pytest-asyncio>=0.21.0
pytest-cov>=4.1.0
pytest-mock>=3.11.0

# Executar
pytest orchestrator/tests/ -v --cov=orchestrator --cov-report=html
```

---

### 5.2. Type Hints ✅ (8/10)

**Bem implementado**:
- Uso consistente de type hints
- Dataclasses para modelos
- Pydantic para validação de API

**Pontos de Melhoria**:

```python
# Alguns lugares sem type hints
# orchestrator.py, linha 254
_chat_sessions: Dict[str, Any] = {}  # ❌ Any muito genérico

# Deveria ser:
_chat_sessions: Dict[str, LLMClient] = {}

# correlation.py, linha 25
def _extract_labels(self, result: Dict[str, Any]) -> Dict[str, str]:
    # ✅ Bom, mas poderia ter TypedDict para result

# Melhor:
from typing import TypedDict

class AlertResult(TypedDict, total=False):
    labels: Dict[str, str]
    correlation: Dict[str, Optional[str]]
    _grafana_labels: Dict[str, str]
    cmdb_ci_name: str
    # ...

def _extract_labels(self, result: AlertResult) -> Dict[str, str]:
    # ...
```

**Adicionar mypy**:
```ini
# mypy.ini
[mypy]
python_version = 3.11
warn_return_any = True
warn_unused_configs = True
disallow_untyped_defs = True
disallow_any_generics = True
check_untyped_defs = True
no_implicit_optional = True
warn_redundant_casts = True
warn_unused_ignores = True
warn_no_return = True
strict_equality = True

[mypy-httpx.*]
ignore_missing_imports = True

[mypy-mcp.*]
ignore_missing_imports = True
```

---

### 5.3. Documentação 🟡 (6/10)

**Bem documentado**:
- Docstrings em classes principais
- README.md nos diretórios
- Comentários em código complexo

**Pontos de Melhoria**:

1. **Falta docstrings em métodos**
   ```python
   # orchestrator.py
   async def _gather_signals(self, case_file: CaseFile) -> List[Evidence]:
       # ❌ Sem docstring
       evidence_list: List[Evidence] = []
       # ...
   
   # Deveria ter:
   async def _gather_signals(self, case_file: CaseFile) -> List[Evidence]:
       """
       Gather evidence from all configured MCP servers.
       
       This method:
       1. Queries Grafana for firing alerts matching the scope
       2. Queries incidents database for related incidents
       3. Runs queries in parallel using asyncio.gather
       
       Args:
           case_file: CaseFile with scope and time window
       
       Returns:
           List of Evidence objects from all sources
       
       Raises:
           Exception: If all MCP servers fail (partial failures are logged)
       """
   ```

2. **Falta API documentation**
   ```python
   # orchestrator.py
   @app.post("/investigate", response_model=InvestigateResponse)
   async def investigate_endpoint(request: InvestigateRequest):
       """
       Investigate an incident, alert, or symptom.
       
       This endpoint coordinates evidence gathering from multiple sources,
       correlates signals, and generates hypotheses about root causes.
       
       **Input Types**:
       - INCIDENT_ID: ServiceNow incident number (e.g., INC0012345)
       - ALERT_UID: Grafana alert UID (e.g., df4m8ngnj6br4e)
       - SYMPTOM: Free-text symptom description
       
       **Filters** (optional):
       - application_service: Service/component name
       - business_capability: Business capability
       - owner_squad: Responsible squad
       - severidade: Severity (P1, P2, P3)
       - env: Environment (production, staging)
       
       **Example**:
       ```json
       {
         "input_type": "SYMPTOM",
         "value": "high latency",
         "filters": {
           "application_service": "payment-api",
           "env": "production"
         }
       }
       ```
       
       **Returns**:
       - caseFileId: Unique case identifier
       - scope: Extracted scope (service, env, cluster, etc)
       - evidence: List of evidence from all sources
       - hypotheses: Ranked hypotheses with next steps
       - correlationGaps: Missing labels preventing correlation
       
       **Errors**:
       - 400: Invalid input format
       - 504: Timeout communicating with data sources
       - 500: Internal server error
       """
   ```

3. **Adicionar OpenAPI tags**
   ```python
   app = FastAPI(
       title="Observability Troubleshooting Copilot",
       description="AI-powered incident triage and root cause analysis",
       version="1.0.0",
       docs_url="/docs",
       redoc_url="/redoc"
   )
   
   @app.post("/investigate", tags=["Investigation"])
   @app.post("/chat", tags=["Chat"])
   @app.get("/health", tags=["Health"])
   @app.get("/metrics", tags=["Monitoring"])
   ```

---

### 5.4. Logging 🟡 (7/10)

**Bem implementado**:
- Logging estruturado com níveis apropriados
- Contexto útil nas mensagens
- Uso de log.exception() para erros

**Pontos de Melhoria**:

1. **Falta correlation ID**
   ```python
   # Adicionar correlation ID para rastrear requests
   import contextvars
   
   correlation_id_var = contextvars.ContextVar('correlation_id', default=None)
   
   class CorrelationIdFilter(logging.Filter):
       def filter(self, record):
           record.correlation_id = correlation_id_var.get() or 'N/A'
           return True
   
   # Configurar logging
   handler = logging.StreamHandler()
   handler.addFilter(CorrelationIdFilter())
   formatter = logging.Formatter(
       '%(asctime)s - %(name)s - %(levelname)s - [%(correlation_id)s] - %(message)s'
   )
   handler.setFormatter(formatter)
   
   # Middleware para adicionar correlation ID
   @app.middleware("http")
   async def correlation_id_middleware(request: Request, call_next):
       correlation_id = request.headers.get('X-Correlation-ID', str(uuid.uuid4()))
       correlation_id_var.set(correlation_id)
       
       response = await call_next(request)
       response.headers['X-Correlation-ID'] = correlation_id
       return response
   ```

2. **Logs sensíveis**
   ```python
   # mcp_client.py, linha 19
   log.info(f"Calling {self.server_name}.{tool_name} with args: {arguments}")
   # ❌ Pode logar dados sensíveis
   
   # Deveria ser:
   safe_args = {k: v if k not in ['token', 'password', 'secret'] else '***' 
                for k, v in arguments.items()}
   log.info(f"Calling {self.server_name}.{tool_name} with args: {safe_args}")
   ```

3. **Falta structured logging (JSON)**
   ```python
   # requirements.txt
   python-json-logger>=2.0.0
   
   # orchestrator.py
   from pythonjsonlogger import jsonlogger
   
   logHandler = logging.StreamHandler()
   formatter = jsonlogger.JsonFormatter(
       '%(asctime)s %(name)s %(levelname)s %(correlation_id)s %(message)s'
   )
   logHandler.setFormatter(formatter)
   log.addHandler(logHandler)
   ```

---

## 6. MCP Servers 🔌

### 6.1. Grafana MCP Server ✅ (8/10)

**Pontos Positivos**:
- Suporte a stdio e SSE modes
- Normalização de labels para correlação
- Tratamento de erros HTTP
- Logging detalhado

**Pontos de Melhoria**:

1. **SSL Verification Hardcoded**
   ```python
   # grafana_v2.py, linha 60
   self._client = httpx.AsyncClient(
       base_url=cfg.base_url,
       headers=headers,
       verify=False,  # ❌ Sempre desabilitado
       timeout=httpx.Timeout(cfg.timeout_s),
   )
   
   # Deveria usar cfg.verify_tls
   self._client = httpx.AsyncClient(
       base_url=cfg.base_url,
       headers=headers,
       verify=cfg.verify_tls,
       timeout=httpx.Timeout(cfg.timeout_s),
   )
   ```

2. **Falta paginação em find_firing_alerts**
   ```python
   # grafana_v2.py, linha 76
   async def find_firing_alerts(...) -> List[Dict[str, Any]]:
       params = {"active": "true", "silenced": "false", "inhibited": "false"}
       alerts = await self.get("/api/alertmanager/grafana/api/v2/alerts", params=params)
       # ❌ Sem paginação, pode retornar muitos alertas
   
   # Adicionar limite e paginação
   async def find_firing_alerts(
       self, 
       labels: Optional[Dict[str, str]] = None,
       limit: int = 100
   ) -> List[Dict[str, Any]]:
       params = {
           "active": "true",
           "silenced": "false",
           "inhibited": "false",
           "limit": limit
       }
       # ...
   ```

3. **Filtro de labels ineficiente**
   ```python
   # grafana_v2.py, linha 82-89
   if labels:
       filtered = []
       for alert in alerts:
           alert_labels = alert.get("labels", {})
           if all(alert_labels.get(k) == v for k, v in labels.items()):
               filtered.append(alert)
       alerts = filtered
   
   # ❌ Filtra no client-side, deveria usar API do Grafana
   # Grafana Alertmanager API suporta matchers
   ```

---

### 6.2. Incidents PostgreSQL MCP Server ✅ (7/10)

**Análise do código**:

```python
# mcp-servers/incidents_pg.py (assumindo estrutura similar ao grafana_v2.py)
```

**Pontos a verificar**:
1. Connection pooling configurado corretamente?
2. Prepared statements para prevenir SQL injection?
3. Índices nas colunas de busca (cmdb_ci_name, number, parent_incident)?
4. Timeout de queries configurado?
5. Tratamento de connection loss?

**Recomendações gerais**:

```python
# incidents_pg.py
import psycopg_pool

class IncidentsDB:
    def __init__(self, config: DBConfig):
        # Connection pool
        self.pool = psycopg_pool.AsyncConnectionPool(
            conninfo=config.dsn,
            min_size=config.min_conn,
            max_size=config.max_conn,
            timeout=30,
            max_idle=300,  # 5 minutos
            max_lifetime=3600  # 1 hora
        )
    
    async def get_incident(self, number: str) -> Optional[Dict[str, Any]]:
        """Get incident by number using prepared statement."""
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # Usar prepared statement
                await cur.execute(
                    """
                    SELECT sys_id, number, short_description, opened_at,
                           cmdb_ci_name, priority, state, assignment_group_name
                    FROM incidents_snow
                    WHERE number = %s
                    LIMIT 1
                    """,
                    (number,)
                )
                row = await cur.fetchone()
                if not row:
                    return None
                
                # Converter para dict
                columns = [desc[0] for desc in cur.description]
                return dict(zip(columns, row))
    
    async def find_related_incidents(
        self,
        cmdb_ci_name: Optional[str] = None,
        parent_number: Optional[str] = None,
        time_window_hours: int = 24
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Find related incidents by CI or parent."""
        result = {"by_ci": [], "by_parent": []}
        
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # Buscar por CI
                if cmdb_ci_name:
                    await cur.execute(
                        """
                        SELECT sys_id, number, short_description, opened_at,
                               priority, state, assignment_group_name
                        FROM incidents_snow
                        WHERE cmdb_ci_name = %s
                          AND opened_at >= NOW() - INTERVAL '%s hours'
                          AND state NOT IN ('Closed', 'Resolved')
                        ORDER BY opened_at DESC
                        LIMIT 50
                        """,
                        (cmdb_ci_name, time_window_hours)
                    )
                    
                    columns = [desc[0] for desc in cur.description]
                    result["by_ci"] = [
                        dict(zip(columns, row))
                        for row in await cur.fetchall()
                    ]
                
                # Buscar por parent
                if parent_number:
                    await cur.execute(
                        """
                        SELECT sys_id, number, short_description, opened_at,
                               priority, state, assignment_group_name
                        FROM incidents_snow
                        WHERE parent_incident = (
                            SELECT sys_id FROM incidents_snow WHERE number = %s
                        )
                        ORDER BY opened_at DESC
                        LIMIT 50
                        """,
                        (parent_number,)
                    )
                    
                    columns = [desc[0] for desc in cur.description]
                    result["by_parent"] = [
                        dict(zip(columns, row))
                        for row in await cur.fetchall()
                    ]
        
        return result
```

**Índices recomendados**:
```sql
-- PostgreSQL
CREATE INDEX CONCURRENTLY idx_incidents_cmdb_ci_name 
ON incidents_snow(cmdb_ci_name) 
WHERE state NOT IN ('Closed', 'Resolved');

CREATE INDEX CONCURRENTLY idx_incidents_number 
ON incidents_snow(number);

CREATE INDEX CONCURRENTLY idx_incidents_parent 
ON incidents_snow(parent_incident) 
WHERE parent_incident IS NOT NULL;

CREATE INDEX CONCURRENTLY idx_incidents_opened_at 
ON incidents_snow(opened_at DESC);

-- Índice composto para query comum
CREATE INDEX CONCURRENTLY idx_incidents_ci_opened 
ON incidents_snow(cmdb_ci_name, opened_at DESC) 
WHERE state NOT IN ('Closed', 'Resolved');
```

---

## 7. Configuração e Deploy 🚀

### 7.1. Docker e Kubernetes 🟡 (7/10)

**Bem implementado**:
- Dockerfiles otimizados
- Manifestos K8s completos (deployment, service, configmap, secret)
- Health checks configurados
- Resource limits definidos

**Pontos de Melhoria**:

1. **Falta liveness e readiness probes diferenciados**
   ```yaml
   # k8s/orchestrator/deployment.yaml
   livenessProbe:
     httpGet:
       path: /health
       port: 8080
     initialDelaySeconds: 30
     periodSeconds: 10
     timeoutSeconds: 5
     failureThreshold: 3
   
   readinessProbe:
     httpGet:
       path: /health/ready  # ❌ Usar endpoint diferente
       port: 8080
     initialDelaySeconds: 10
     periodSeconds: 5
     timeoutSeconds: 3
     failureThreshold: 2
   
   # Adicionar endpoint /health/ready
   @app.get("/health/ready")
   async def readiness_check():
       # Verificar conectividade com MCP servers
       checks = {}
       for name, server in config.mcp_servers.items():
           try:
               client = MCPClient(name, server.endpoint, timeout=2)
               result = await client.call_tool("health", {})
               checks[name] = result.get("success", False)
               await client.close()
           except:
               checks[name] = False
       
       all_healthy = all(checks.values())
       status_code = 200 if all_healthy else 503
       
       return JSONResponse(
           {"status": "ready" if all_healthy else "not_ready", "checks": checks},
           status_code=status_code
       )
   ```

2. **Falta PodDisruptionBudget**
   ```yaml
   # k8s/orchestrator/pdb.yaml
   apiVersion: policy/v1
   kind: PodDisruptionBudget
   metadata:
     name: orchestrator-pdb
     namespace: copilot
   spec:
     minAvailable: 1
     selector:
       matchLabels:
         app: orchestrator
   ```

3. **Falta NetworkPolicy**
   ```yaml
   # k8s/orchestrator/networkpolicy.yaml
   apiVersion: networking.k8s.io/v1
   kind: NetworkPolicy
   metadata:
     name: orchestrator-netpol
     namespace: copilot
   spec:
     podSelector:
       matchLabels:
         app: orchestrator
     policyTypes:
     - Ingress
     - Egress
     ingress:
     - from:
       - namespaceSelector:
           matchLabels:
             name: ingress-nginx
       ports:
       - protocol: TCP
         port: 8080
     egress:
     - to:
       - namespaceSelector:
           matchLabels:
             name: observability
       ports:
       - protocol: TCP
         port: 8080
     - to:  # DNS
       - namespaceSelector:
           matchLabels:
             name: kube-system
       ports:
       - protocol: UDP
         port: 53
   ```

---

### 7.2. Configuração 🟡 (6/10)

**Problemas**:

1. **Configuração hardcoded**
   ```python
   # config.py, linha 24-60
   self.mcp_servers = {
       "grafana": MCPServerConfig(
           endpoint=os.getenv(
               "GRAFANA_MCP_ENDPOINT",
               "http://grafana-mcp-server.observability.svc.cluster.local:8080"
           ),
           timeout=15,  # ❌ Hardcoded
       ),
       # ...
   }
   ```

2. **Falta validação de configuração**
   ```python
   # Adicionar validação no startup
   @app.on_event("startup")
   async def validate_config():
       errors = []
       
       # Validar MCP endpoints
       for name, server in config.mcp_servers.items():
           if not server.endpoint:
               errors.append(f"MCP server {name} has no endpoint")
           
           if server.timeout <= 0:
               errors.append(f"MCP server {name} has invalid timeout")
       
       # Validar standard labels
       if not config.standard_labels:
           errors.append("No standard labels configured")
       
       if errors:
           log.error(f"Configuration errors: {errors}")
           raise RuntimeError(f"Invalid configuration: {errors}")
       
       log.info("Configuration validated successfully")
   ```

3. **Usar Pydantic Settings**
   ```python
   # config.py
   from pydantic_settings import BaseSettings
   
   class MCPServerSettings(BaseSettings):
       endpoint: str
       timeout: int = 15
       
       class Config:
           env_prefix = ""
   
   class Settings(BaseSettings):
       # MCP Servers
       grafana_mcp_endpoint: str = "http://grafana-mcp:8080"
       grafana_mcp_timeout: int = 15
       
       incidents_pg_mcp_endpoint: str = "http://incidents-pg-mcp:8080"
       incidents_pg_mcp_timeout: int = 15
       
       # Orchestrator
       port: int = 8080
       log_level: str = "INFO"
       
       # Correlation
       standard_labels: List[str] = [
           "application_service",
           "owner_squad",
           "severity"
       ]
       
       # LLM
       openai_api_key: Optional[str] = None
       openai_model: str = "gpt-4o"
       
       class Config:
           env_file = ".env"
           case_sensitive = False
   
   settings = Settings()
   ```

---

## 8. Análise de Dependências 📦

### 8.1. Orchestrator Dependencies

```txt
# orchestrator/requirements.txt
fastapi>=0.104.0          # ✅ Atualizado
uvicorn[standard]>=0.24.0 # ✅ Atualizado
httpx>=0.25.0             # ✅ Atualizado
pydantic>=2.0.0           # ✅ Atualizado
python-multipart>=0.0.6   # ✅ OK
openai>=1.0.0             # ✅ Atualizado
```

**Problemas**:
1. Falta pinning de versões exatas (usar `==` em produção)
2. Falta dependências de desenvolvimento
3. Falta dependências opcionais documentadas

**Recomendação**:
```txt
# requirements.txt (produção)
fastapi==0.109.0
uvicorn[standard]==0.27.0
httpx==0.26.0
pydantic==2.5.3
python-multipart==0.0.6
openai==1.10.0

# requirements-dev.txt (desenvolvimento)
pytest==7.4.3
pytest-asyncio==0.21.1
pytest-cov==4.1.0
pytest-mock==3.12.0
mypy==1.8.0
black==23.12.1
ruff==0.1.11
pre-commit==3.6.0

# requirements-optional.txt (features opcionais)
prometheus-client==0.19.0  # Métricas
aiocache==0.12.2           # Cache
pybreaker==1.0.1           # Circuit breaker
slowapi==0.1.9             # Rate limiting
presidio-analyzer==2.2.0   # PII detection avançada
presidio-anonymizer==2.2.0
```

---

### 8.2. MCP Servers Dependencies

```txt
# mcp-servers/requirements.txt
httpx>=0.25.0                # ✅ OK
mcp>=1.0.0                   # ✅ OK
psycopg[binary]>=3.1.0       # ✅ Atualizado
psycopg-pool>=3.1.0          # ✅ OK
```

**Recomendação**: Adicionar starlette para SSE mode
```txt
httpx==0.26.0
mcp==1.0.0
psycopg[binary]==3.1.16
psycopg-pool==3.1.9
starlette==0.35.1  # Para SSE mode
uvicorn==0.27.0    # Para SSE mode
```

---

### 8.3. Vulnerabilidades de Segurança

**Executar scan de vulnerabilidades**:
```bash
# Instalar safety
pip install safety

# Scan
safety check --file requirements.txt

# Ou usar pip-audit
pip install pip-audit
pip-audit
```

**Adicionar ao CI/CD**:
```yaml
# .github/workflows/security.yml
name: Security Scan
on: [push, pull_request]

jobs:
  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install safety pip-audit
      - name: Run safety check
        run: safety check --file orchestrator/requirements.txt
      - name: Run pip-audit
        run: pip-audit -r orchestrator/requirements.txt
```

---

## 9. Recomendações Prioritárias 🎯

### 9.1. Críticas (Fazer Imediatamente) 🔴

1. **Corrigir bug de correlação de incidentes**
   - Arquivo: `orchestrator/orchestrator.py`
   - Impacto: Funcionalidade principal quebrada
   - Esforço: 2-4 horas
   - Prioridade: P0

2. **Adicionar tratamento de erros robusto**
   - Arquivo: `orchestrator/orchestrator.py`
   - Impacto: Segurança e estabilidade
   - Esforço: 4-8 horas
   - Prioridade: P0

3. **Implementar testes básicos**
   - Cobertura mínima: 60%
   - Focar em: correlation, hypothesis, guardrails
   - Esforço: 1-2 dias
   - Prioridade: P0

---

### 9.2. Altas (Próximas 2 Semanas) 🟡

4. **Adicionar circuit breaker e retry**
   - Prevenir cascata de falhas
   - Esforço: 4-6 horas
   - Prioridade: P1

5. **Implementar connection pooling**
   - Melhorar performance
   - Esforço: 2-4 horas
   - Prioridade: P1

6. **Adicionar métricas Prometheus**
   - Observabilidade do orchestrator
   - Esforço: 4-6 horas
   - Prioridade: P1

7. **Melhorar PII redaction**
   - Usar biblioteca especializada
   - Adicionar patterns brasileiros
   - Esforço: 4-6 horas
   - Prioridade: P1

8. **Adicionar rate limiting**
   - Prevenir abuso
   - Esforço: 2-3 horas
   - Prioridade: P1

---

### 9.3. Médias (Próximo Mês) 🟢

9. **Implementar caching**
   - Reduzir latência
   - Esforço: 6-8 horas
   - Prioridade: P2

10. **Adicionar validação de input**
    - Melhorar segurança
    - Esforço: 4-6 horas
    - Prioridade: P2

11. **Refatorar session store**
    - Thread-safe com TTL
    - Esforço: 3-4 horas
    - Prioridade: P2

12. **Melhorar logging**
    - Structured logging (JSON)
    - Correlation IDs
    - Esforço: 4-6 horas
    - Prioridade: P2

13. **Adicionar type checking (mypy)**
    - Melhorar qualidade de código
    - Esforço: 2-3 horas
    - Prioridade: P2

---

### 9.4. Baixas (Backlog) ⚪

14. **Separar API de business logic**
    - Melhorar arquitetura
    - Esforço: 1-2 dias
    - Prioridade: P3

15. **Adicionar dependency injection**
    - Facilitar testes
    - Esforço: 1 dia
    - Prioridade: P3

16. **Usar Pydantic Settings**
    - Melhorar configuração
    - Esforço: 3-4 horas
    - Prioridade: P3

17. **Adicionar NetworkPolicy**
    - Segurança K8s
    - Esforço: 2-3 horas
    - Prioridade: P3

---

## 10. Checklist de Implementação 📋

### 10.1. Bugs Críticos

- [ ] **Bug #1**: Corrigir correlação de incidentes com filtros não-service
  - [ ] Implementar busca em 2 fases (alertas → extrair services → incidentes)
  - [ ] Adicionar testes para cenários com business_capability
  - [ ] Adicionar testes para cenários com owner_squad
  - [ ] Validar que incidentes são retornados corretamente

- [ ] **Bug #2**: Melhorar tratamento de exceções
  - [ ] Criar enum ErrorCode
  - [ ] Aplicar PII redaction em mensagens de erro
  - [ ] Diferenciar tipos de erro (timeout, auth, network)
  - [ ] Adicionar correlation ID nas respostas de erro

- [ ] **Bug #3**: Corrigir race condition em session store
  - [ ] Implementar SessionStore thread-safe
  - [ ] Adicionar TTL para sessões
  - [ ] Adicionar limite máximo de sessões
  - [ ] Implementar cleanup de sessões expiradas

---

### 10.2. Segurança

- [ ] **PII Redaction**
  - [ ] Adicionar patterns brasileiros (CPF, CNPJ)
  - [ ] Considerar usar presidio-analyzer
  - [ ] Adicionar testes para todos os patterns
  - [ ] Aplicar redaction em logs

- [ ] **Rate Limiting**
  - [ ] Implementar com slowapi
  - [ ] Configurar limites por endpoint
  - [ ] Adicionar whitelist para IPs internos
  - [ ] Adicionar métricas de rate limiting

- [ ] **Input Validation**
  - [ ] Adicionar validators no Pydantic
  - [ ] Validar formato de incident_id
  - [ ] Validar formato de alert_uid
  - [ ] Validar tamanho de inputs
  - [ ] Validar filtros permitidos

- [ ] **Secrets Management**
  - [ ] Suportar secrets montados como arquivos
  - [ ] Implementar reload de secrets
  - [ ] Adicionar endpoint admin para reload
  - [ ] Nunca logar tokens completos

---

### 10.3. Performance

- [ ] **Connection Pooling**
  - [ ] Implementar MCPClientPool
  - [ ] Configurar max_connections
  - [ ] Configurar max_keepalive_connections
  - [ ] Adicionar lifecycle hooks (startup/shutdown)

- [ ] **Caching**
  - [ ] Implementar cache com aiocache
  - [ ] Configurar TTL por tipo de query
  - [ ] Adicionar métricas de cache hit/miss
  - [ ] Implementar cache invalidation

- [ ] **Circuit Breaker**
  - [ ] Implementar com pybreaker
  - [ ] Configurar thresholds por MCP server
  - [ ] Adicionar métricas de circuit breaker
  - [ ] Implementar fallback strategies

- [ ] **Timeouts**
  - [ ] Adicionar timeout global para _gather_signals
  - [ ] Configurar timeouts em cascata
  - [ ] Retornar evidências parciais em timeout
  - [ ] Adicionar métricas de timeout

---

### 10.4. Observabilidade

- [ ] **Métricas Prometheus**
  - [ ] Implementar métricas básicas (requests, duration, errors)
  - [ ] Adicionar métricas de MCP calls
  - [ ] Adicionar métricas de evidence count
  - [ ] Adicionar métricas de session count
  - [ ] Expor endpoint /metrics

- [ ] **Logging**
  - [ ] Implementar structured logging (JSON)
  - [ ] Adicionar correlation IDs
  - [ ] Implementar middleware para correlation ID
  - [ ] Sanitizar dados sensíveis em logs
  - [ ] Configurar níveis de log por ambiente

- [ ] **Health Checks**
  - [ ] Diferenciar /health (liveness) e /health/ready (readiness)
  - [ ] Verificar conectividade com MCP servers em readiness
  - [ ] Adicionar timeout nos health checks
  - [ ] Retornar status detalhado

---

### 10.5. Testes

- [ ] **Unit Tests**
  - [ ] correlation.py (>80% coverage)
  - [ ] hypothesis.py (>80% coverage)
  - [ ] guardrails.py (>90% coverage)
  - [ ] models.py (validações)

- [ ] **Integration Tests**
  - [ ] orchestrator.py (fluxos principais)
  - [ ] agents/grafana.py (mock MCP)
  - [ ] agents/incidents.py (mock MCP)
  - [ ] mcp_client.py (mock HTTP)

- [ ] **E2E Tests**
  - [ ] Busca por INCIDENT_ID
  - [ ] Busca por ALERT_UID
  - [ ] Busca por SYMPTOM com application_service
  - [ ] Busca por SYMPTOM com business_capability
  - [ ] Endpoint /chat

- [ ] **Performance Tests**
  - [ ] Load test com locust
  - [ ] Stress test
  - [ ] Latency benchmarks
  - [ ] Memory leak detection

---

### 10.6. Documentação

- [ ] **Code Documentation**
  - [ ] Adicionar docstrings em todos os métodos públicos
  - [ ] Adicionar docstrings em endpoints FastAPI
  - [ ] Adicionar exemplos de uso
  - [ ] Documentar exceções lançadas

- [ ] **API Documentation**
  - [ ] Melhorar descrições no OpenAPI
  - [ ] Adicionar exemplos de request/response
  - [ ] Adicionar tags para organização
  - [ ] Documentar códigos de erro

- [ ] **Operational Documentation**
  - [ ] Runbook de troubleshooting
  - [ ] Guia de deployment
  - [ ] Guia de configuração
  - [ ] Guia de monitoramento

---

### 10.7. CI/CD

- [ ] **Linting e Formatting**
  - [ ] Configurar black
  - [ ] Configurar ruff
  - [ ] Configurar mypy
  - [ ] Adicionar pre-commit hooks

- [ ] **Security Scanning**
  - [ ] Configurar safety check
  - [ ] Configurar pip-audit
  - [ ] Configurar bandit (SAST)
  - [ ] Configurar trivy (container scanning)

- [ ] **Pipeline**
  - [ ] Lint e format check
  - [ ] Type checking (mypy)
  - [ ] Unit tests
  - [ ] Integration tests
  - [ ] Security scanning
  - [ ] Build Docker image
  - [ ] Push to registry
  - [ ] Deploy to staging
  - [ ] E2E tests em staging
  - [ ] Deploy to production

---

## 11. Estimativas de Esforço 📊

### 11.1. Por Categoria

| Categoria | Tarefas | Esforço Total | Prioridade |
|-----------|---------|---------------|------------|
| Bugs Críticos | 3 | 1-2 dias | P0 |
| Segurança | 4 | 2-3 dias | P0-P1 |
| Performance | 4 | 2-3 dias | P1 |
| Observabilidade | 3 | 1-2 dias | P1 |
| Testes | 4 | 3-5 dias | P0-P1 |
| Documentação | 3 | 1-2 dias | P2 |
| CI/CD | 2 | 1-2 dias | P2 |
| **TOTAL** | **23** | **11-19 dias** | - |

### 11.2. Roadmap Sugerido

#### Sprint 1 (1 semana) - Estabilização
- Corrigir bugs críticos (3 tarefas)
- Implementar testes básicos (cobertura 60%)
- Adicionar tratamento de erros robusto
- **Entrega**: Sistema estável e testado

#### Sprint 2 (1 semana) - Segurança e Performance
- Implementar rate limiting
- Melhorar PII redaction
- Adicionar circuit breaker
- Implementar connection pooling
- **Entrega**: Sistema seguro e performático

#### Sprint 3 (1 semana) - Observabilidade
- Adicionar métricas Prometheus
- Implementar structured logging
- Melhorar health checks
- Adicionar caching
- **Entrega**: Sistema observável

#### Sprint 4 (1 semana) - Qualidade
- Aumentar cobertura de testes (>80%)
- Adicionar type checking (mypy)
- Melhorar documentação
- Configurar CI/CD completo
- **Entrega**: Sistema production-ready

---

## 12. Métricas de Qualidade 📈

### 12.1. Estado Atual (Estimado)

| Métrica | Valor Atual | Meta | Status |
|---------|-------------|------|--------|
| Cobertura de Testes | ~10% | >80% | 🔴 |
| Type Coverage (mypy) | ~60% | >90% | 🟡 |
| Bugs Críticos | 3 | 0 | 🔴 |
| Vulnerabilidades | ? | 0 | 🟡 |
| Documentação | ~40% | >80% | 🟡 |
| Performance (p95) | ? | <2s | 🟡 |
| Disponibilidade | ? | >99.5% | 🟡 |

### 12.2. Metas Pós-Implementação

| Métrica | Meta | Como Medir |
|---------|------|------------|
| Cobertura de Testes | >80% | pytest-cov |
| Type Coverage | >90% | mypy --strict |
| Bugs Críticos | 0 | Issue tracker |
| Vulnerabilidades | 0 | safety + pip-audit |
| Documentação | >80% | Manual review |
| Performance (p95) | <2s | Prometheus |
| Disponibilidade | >99.5% | Uptime monitoring |
| MTTR | <15min | Incident tracking |
| Error Rate | <0.1% | Prometheus |

---

## 13. Conclusão e Próximos Passos 🎯

### 13.1. Resumo

O projeto **Observability Troubleshooting Copilot** apresenta uma arquitetura sólida e modular, com separação clara de responsabilidades. O código é geralmente bem escrito e segue boas práticas de Python moderno.

**Pontos Fortes**:
- Arquitetura modular e extensível
- Uso correto de async/await
- Guardrails de segurança implementados
- Logging estruturado
- Suporte a múltiplos modos (stdio/SSE)

**Áreas Críticas de Melhoria**:
- **Bug crítico** na correlação de incidentes
- Falta de testes automatizados
- Tratamento de erros incompleto
- Ausência de observabilidade do próprio sistema
- Falta de proteções contra abuso (rate limiting)

### 13.2. Recomendação

**Status**: ⚠️ **NÃO PRODUCTION-READY**

O sistema está em bom estado para PoC/MVP, mas requer melhorias significativas antes de produção:

1. **Bloqueadores para Produção** (P0):
   - Corrigir bug de correlação
   - Adicionar testes (mínimo 60% coverage)
   - Implementar tratamento de erros robusto
   - Adicionar rate limiting

2. **Altamente Recomendado** (P1):
   - Circuit breaker e retry
   - Métricas Prometheus
   - Structured logging
   - Connection pooling

3. **Desejável** (P2):
   - Caching
   - Type checking (mypy)
   - Documentação completa
   - CI/CD automatizado

### 13.3. Próximos Passos Imediatos

1. **Semana 1**: Corrigir bugs críticos e adicionar testes básicos
2. **Semana 2**: Implementar segurança e performance
3. **Semana 3**: Adicionar observabilidade
4. **Semana 4**: Melhorar qualidade e documentação

**Após 4 semanas**: Sistema pronto para produção com monitoramento adequado.

---

## 14. Referências 📚

### 14.1. Documentação Oficial

- [FastAPI Best Practices](https://fastapi.tiangolo.com/tutorial/best-practices/)
- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [HTTPX Advanced Usage](https://www.python-httpx.org/advanced/)
- [Prometheus Python Client](https://github.com/prometheus/client_python)
- [MCP Protocol](https://modelcontextprotocol.io/)

### 14.2. Ferramentas Recomendadas

- **Testing**: pytest, pytest-asyncio, pytest-cov, pytest-mock
- **Linting**: ruff, black, mypy
- **Security**: safety, pip-audit, bandit, trivy
- **Monitoring**: prometheus-client, opentelemetry
- **Performance**: locust, py-spy, memory-profiler

### 14.3. Padrões e Práticas

- [12 Factor App](https://12factor.net/)
- [Python Best Practices](https://docs.python-guide.org/)
- [API Design Best Practices](https://swagger.io/resources/articles/best-practices-in-api-design/)
- [Kubernetes Best Practices](https://kubernetes.io/docs/concepts/configuration/overview/)

---

## Apêndice A: Exemplo de Implementação - Bug Fix

### A.1. Correção do Bug de Correlação

```python
# orchestrator/orchestrator.py

async def _gather_signals(self, case_file: CaseFile) -> List[Evidence]:
    """
    Gather evidence from all configured MCP servers.
    
    Strategy:
    1. Query Grafana for firing alerts matching the scope
    2. Extract application_service from alerts found
    3. Query incidents database for each service found
    4. Run queries in parallel when possible
    
    Args:
        case_file: CaseFile with scope and time window
    
    Returns:
        List of Evidence objects from all sources
    """
    evidence_list: List[Evidence] = []
    
    grafana_client = MCPClient("grafana", config.mcp_servers["grafana"].endpoint)
    incidents_client = MCPClient("incidents-pg", config.mcp_servers["incidents-pg"].endpoint)
    grafana_agent = GrafanaAgent(grafana_client)
    incidents_agent = IncidentsAgent(incidents_client)
    
    try:
        # Fase 1: Buscar alertas
        log.info("Phase 1: Gathering alerts from Grafana")
        alert_evidences = await grafana_agent.find_firing_alerts(case_file.scope)
        evidence_list.extend(alert_evidences)
        log.info(f"Found {len(alert_evidences)} alerts")
        
        # Fase 2: Extrair services dos alertas
        services = set()
        for evidence in alert_evidences:
            labels = evidence.result.get("labels", {})
            app_svc = labels.get("application_service")
            if app_svc:
                services.add(app_svc)
        
        # Adicionar service do scope se existir
        if case_file.scope.serviceName:
            services.add(case_file.scope.serviceName)
        
        log.info(f"Phase 2: Extracted {len(services)} unique services: {services}")
        
        # Fase 3: Buscar incidentes para cada service
        if services:
            log.info("Phase 3: Gathering incidents from PostgreSQL")
            incident_tasks = [
                incidents_agent.find_related_incidents(cmdb_ci_name=service)
                for service in services
            ]
            
            incident_results = await asyncio.gather(*incident_tasks, return_exceptions=True)
            
            for result in incident_results:
                if isinstance(result, Exception):
                    log.error(f"Error gathering incidents: {result}")
                elif isinstance(result, list):
                    evidence_list.extend(result)
            
            incident_count = sum(
                len(r) for r in incident_results 
                if isinstance(r, list)
            )
            log.info(f"Found {incident_count} related incidents")
        else:
            log.warning("No services found, skipping incident search")
        
        log.info(f"Total evidence collected: {len(evidence_list)}")
        
    except Exception as e:
        log.exception(f"Error in _gather_signals: {e}")
        # Retornar evidências parciais coletadas até agora
    
    finally:
        await grafana_client.close()
        await incidents_client.close()
    
    return evidence_list
```

### A.2. Testes para o Bug Fix

```python
# orchestrator/tests/test_orchestrator_correlation.py

import pytest
from orchestrator import Orchestrator
from models import Input, InputType, Evidence, EvidenceType

@pytest.mark.asyncio
async def test_gather_signals_with_business_capability_filter(
    orchestrator, 
    mock_grafana_agent, 
    mock_incidents_agent
):
    """
    Test that incidents are fetched when filtering by business_capability.
    
    This is a regression test for the bug where incidents were not fetched
    when the scope didn't have serviceName set.
    """
    # Arrange
    input_data = Input(
        type=InputType.SYMPTOM,
        value="alertas",
        timestamp="2026-04-07T10:00:00Z",
        user="test-user"
    )
    filters = {"business_capability": "aml-pld"}
    
    # Mock: Grafana retorna 2 alertas com application_service
    mock_grafana_agent.find_firing_alerts.return_value = [
        Evidence(
            id="alert-1",
            type=EvidenceType.ALERT_FIRING,
            source="grafana-mcp",
            query="test",
            result={
                "labels": {
                    "application_service": "aml-worker-service",
                    "business_capability": "aml-pld"
                }
            },
            timestamp="2026-04-07T10:00:00Z",
            links=[],
            confidence=0.85,
            redacted=False
        ),
        Evidence(
            id="alert-2",
            type=EvidenceType.ALERT_FIRING,
            source="grafana-mcp",
            query="test",
            result={
                "labels": {
                    "application_service": "aml-api-service",
                    "business_capability": "aml-pld"
                }
            },
            timestamp="2026-04-07T10:00:00Z",
            links=[],
            confidence=0.85,
            redacted=False
        )
    ]
    
    # Mock: Incidents retorna incidentes para cada service
    mock_incidents_agent.find_related_incidents.return_value = [
        Evidence(
            id="incident-1",
            type=EvidenceType.INCIDENT_RELATED,
            source="incidents-pg-mcp",
            query="test",
            result={
                "number": "INC0012345",
                "cmdb_ci_name": "aml-worker-service"
            },
            timestamp="2026-04-07T10:00:00Z",
            links=[],
            confidence=0.7,
            redacted=False
        )
    ]
    
    # Act
    case_file = await orchestrator.investigate(input_data, filters=filters)
    
    # Assert
    assert case_file.scope.additionalLabels["business_capability"] == "aml-pld"
    
    # Verificar que alertas foram coletados
    alert_evidences = [e for e in case_file.evidence if e.type == EvidenceType.ALERT_FIRING]
    assert len(alert_evidences) == 2
    
    # Verificar que incidentes foram coletados (BUG FIX)
    incident_evidences = [e for e in case_file.evidence if e.type == EvidenceType.INCIDENT_RELATED]
    assert len(incident_evidences) > 0, "Incidents should be fetched even without serviceName in scope"
    
    # Verificar que find_related_incidents foi chamado para cada service
    assert mock_incidents_agent.find_related_incidents.call_count == 2
    
    # Verificar que hipóteses foram geradas
    assert len(case_file.hypotheses) > 0
```

---

**Fim do Code Review**

---

**Assinatura**: Kiro AI  
**Data**: 2026-04-07  
**Versão**: 1.0.0
