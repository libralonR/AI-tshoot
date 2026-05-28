# Adicionando novos MCP Servers — Versão Hexagonal

Guia passo a passo para integrar uma nova fonte de dados (MCP server) ao
orchestrator hexagonal. O processo é desenhado para ser **mecânico e isolado**:
cada passo toca um único arquivo, sem efeito colateral nos demais.

---

## Visão geral

A arquitetura hexagonal separa o "o que o sistema precisa" (ports) do "como
ele obtém" (adapters). Para adicionar um novo MCP, você cria:

```
1. Port (contrato)         → application/ports/
2. Adapter (implementação) → infrastructure/adapters/
3. Config (endpoint)       → infrastructure/config.py
4. DI (injeção)            → api/dependencies.py
5. Tools LLM (chat)        → infrastructure/adapters/tools_catalog.py
6. Use case (investigate)  → application/use_cases/investigate.py (opcional)
```

Tempo estimado: **30-60 minutos** para um MCP simples (só /chat).
Se quiser que o `/investigate` também use automaticamente, adicione ~30 min.

---

## Passo 1 — Criar o Port (contrato)

**Arquivo**: `application/ports/<nome>_source.py`

O port define **o que** o orchestrator precisa da nova fonte, sem dizer como.
Use `typing.Protocol` para que qualquer classe que implemente os métodos
satisfaça o contrato automaticamente (duck typing estrutural).

```python
# application/ports/log_source.py
"""Port para fontes de logs (Splunk, Parquet, Loki, etc.)."""

from typing import Any, Dict, List, Optional, Protocol

from domain.models import Evidence


class LogSource(Protocol):
    """Contrato para qualquer adapter que provê logs."""

    async def search_logs(
        self,
        query: str,
        application_service: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 100,
    ) -> List[Evidence]:
        ...

    async def find_error_patterns(
        self,
        application_service: str,
        start: str,
        end: Optional[str] = None,
        top_n: int = 10,
    ) -> List[Evidence]:
        ...
```

**Regras**:
- Retorne `Evidence` ou `List[Evidence]` (domínio puro).
- Não importe nada de `infrastructure/`.
- Nomes de métodos devem ser genéricos (não "splunk_search", mas "search_logs").

Depois, exporte no `application/ports/__init__.py`:

```python
from application.ports.log_source import LogSource
```

---

## Passo 2 — Criar o Adapter (implementação)

**Arquivo**: `infrastructure/adapters/<nome>_adapter.py`

O adapter implementa o port usando o `MCPClient` para falar com o MCP server
externo. Ele é a única camada que conhece detalhes de protocolo (REST, JSON-RPC,
nomes de tools do MCP).

```python
# infrastructure/adapters/splunk_log_adapter.py
"""Adapter Splunk → LogSource."""

import json
import logging
import uuid
from datetime import datetime
from typing import List, Optional

from domain.guardrails import Guardrails
from domain.models import Evidence, EvidenceType
from infrastructure.mcp_client import MCPClient

log = logging.getLogger("orchestrator")


class SplunkLogAdapter:
    """LogSource implementation backed by Splunk MCP."""

    def __init__(self, mcp_client: MCPClient):
        self.mcp = mcp_client

    async def search_logs(
        self,
        query: str,
        application_service: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 100,
    ) -> List[Evidence]:
        args = {"query": query, "max_results": limit}
        if start:
            args["earliest_time"] = start
        if end:
            args["latest_time"] = end

        result = await self.mcp.call_tool("search", args)
        if not result.get("success"):
            log.error(f"Splunk search failed: {result.get('error')}")
            return []

        # PII redaction + Evidence creation
        evidences = []
        for item in result.get("result", []):
            item_str = json.dumps(item)
            redacted_str, was_redacted = Guardrails.redact_pii(item_str)
            evidences.append(
                Evidence(
                    id=str(uuid.uuid4()),
                    type=EvidenceType.LOG_ERROR,
                    source="splunk-mcp",
                    query=query,
                    result=json.loads(redacted_str),
                    timestamp=datetime.utcnow().isoformat(),
                    links=[],
                    confidence=0.7,
                    redacted=was_redacted,
                )
            )
        return evidences

    async def find_error_patterns(self, application_service: str, start: str, end=None, top_n=10):
        result = await self.mcp.call_tool("errors", {
            "application_service": application_service,
            "earliest_time": start,
            "latest_time": end or "now",
            "top_n": top_n,
        })
        # ... mesmo padrão: PII redaction + Evidence list
        ...
```

**Regras**:
- Importe `MCPClient` de `infrastructure.mcp_client`.
- Aplique `Guardrails.redact_pii()` em todo resultado antes de retornar.
- Retorne `Evidence` com `source="<nome>-mcp"` para rastreabilidade.
- Não importe nada de `api/` ou `application/use_cases/`.

Registre no `infrastructure/adapters/__init__.py`:

```python
from infrastructure.adapters.splunk_log_adapter import SplunkLogAdapter
```

---

## Passo 3 — Registrar endpoint no Config

**Arquivo**: `infrastructure/config.py`

Adicione o novo MCP server no dicionário `mcp_servers`:

```python
"splunk": MCPServerConfig(
    endpoint=os.getenv(
        "SPLUNK_MCP_ENDPOINT",
        "http://splunk-mcp-server.observability.svc.cluster.local:8080",
    ),
    timeout=60,
),
```

**Regras**:
- Use env var com default apontando para o service K8s.
- Timeout deve refletir a latência esperada do MCP (15s para rápidos, 60s para Splunk/Athena).

---

## Passo 4 — Injetar no container de DI

**Arquivo**: `api/dependencies.py`

### 4a. Factory do MCPClient

```python
def _splunk_client() -> MCPClient:
    cfg = config.mcp_servers["splunk"]
    return MCPClient("splunk", cfg.endpoint, cfg.timeout)
```

### 4b. Set de tools para o roteador do /chat

```python
SPLUNK_TOOLS = {"splunk_search", "splunk_errors", "splunk_patterns"}
```

### 4c. Routing no `execute_tool()`

Adicione um `elif` no roteador. Se as tools do MCP usam nomes diferentes
dos que o LLM chama (ex: LLM chama `splunk_search`, MCP expõe `search`),
faça o strip do prefixo:

```python
elif tool_name in SPLUNK_TOOLS:
    actual_tool = tool_name.replace("splunk_", "")
    client = _splunk_client()
    server_name = "splunk"
    try:
        result = await client.call_tool(actual_tool, arguments)
        # PII redaction + métricas (mesmo padrão dos outros)
        ...
        return result
    except Exception as e:
        ...
        return {"error": f"Tool execution failed: {str(e)}"}
    finally:
        await client.close()
```

### 4d. (Opcional) Context manager para /investigate

Se o novo MCP também será usado no `/investigate` (Passo 6), crie um
managed adapter:

```python
class _ManagedLogSource:
    def __init__(self):
        self._client = None
        self._adapter = None

    async def __aenter__(self):
        self._client = _splunk_client()
        self._adapter = SplunkLogAdapter(self._client)
        return self._adapter

    async def __aexit__(self, exc_type, exc, tb):
        if self._client:
            await self._client.close()
```

---

## Passo 5 — Adicionar tools no catálogo do LLM

**Arquivo**: `infrastructure/adapters/tools_catalog.py`

Adicione as tool definitions que o LLM vai ver no function calling.
Use nomes **prefixados** quando o MCP tem tools genéricas (ex: `search`)
que colidiriam com outros MCPs:

```python
# Splunk MCP tools
{
    "type": "function",
    "function": {
        "name": "splunk_search",
        "description": "Execute an arbitrary SPL query against Splunk.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "SPL query string"},
                "earliest_time": {"type": "string", "description": "Start time (default -1h)"},
                "latest_time": {"type": "string", "description": "End time (default now)"},
                "max_results": {"type": "integer", "description": "Max results (default 100)"},
            },
            "required": ["query"],
        },
    },
},
```

**Regras**:
- Prefixe com o nome do MCP se houver risco de colisão (`splunk_search`, não `search`).
- Descriptions devem ser claras para o LLM saber quando usar cada tool.
- `required` deve listar apenas o mínimo necessário.

---

## Passo 6 — (Opcional) Integrar no /investigate

Se quiser que o `/investigate` execute queries automaticamente (sem depender
do LLM decidir), altere o `InvestigateUseCase`:

**Arquivo**: `application/use_cases/investigate.py`

### 6a. Adicionar port no construtor

```python
def __init__(
    self,
    ...
    log_source: Optional[LogSource] = None,
    logs_catalog: Optional[List[Dict[str, Any]]] = None,
):
    ...
    self.logs = log_source
    self.logs_catalog = logs_catalog or []
```

### 6b. Adicionar task no `_gather_signals`

```python
# Logs catalog (se houver service + catalog + adapter)
if ci_name and self.logs and self.logs_catalog:
    task_names.append("logs:execute_catalog_queries")
    tasks.append(
        self.logs.execute_catalog_queries(
            service_name=ci_name,
            catalog=self.logs_catalog,
            ...
        )
    )
```

### 6c. Injetar no `_InvestigateContext` (dependencies.py)

```python
class _InvestigateContext:
    async def __aenter__(self) -> InvestigateUseCase:
        ...
        self._logs_cm = _ManagedLogSource()
        self.logs = await self._logs_cm.__aenter__()

        return InvestigateUseCase(
            ...
            log_source=self.logs,
            logs_catalog=getattr(config, "logs_catalog", []),
        )

    async def __aexit__(self, ...):
        await self._logs_cm.__aexit__(...)
        ...
```

---

## Passo 7 — Criar prompt do agente

**Arquivo**: `infrastructure/prompts/<nome>-agent-prompt.md`

Documente:
- Função do agente
- Tools disponíveis (nome, input, output)
- Campos/labels importantes
- Estratégia de correlação com `application_service`
- Casos de uso típicos (3-4 exemplos)
- Regras (read-only, PII, limites)

Siga o padrão de `tempo-agent-prompt.md` ou `metrics-agent-prompt.md`.

---

## Passo 8 — Atualizar orchestrator-prompt.md

**Arquivo**: `infrastructure/prompts/orchestrator-prompt.md`

- Adicionar na seção "Fontes disponíveis"
- Adicionar na "Análise Cruzada Automática" (se integrado no /investigate)
- Adicionar seção no "Formato de resposta" (ex: `📋 Logs`)

---

## Checklist resumido

```
[ ] 1. application/ports/<nome>_source.py — Protocol
[ ] 2. infrastructure/adapters/<nome>_adapter.py — Implementação
[ ] 3. infrastructure/config.py — MCPServerConfig + env var
[ ] 4. api/dependencies.py — factory + TOOLS set + routing
[ ] 5. infrastructure/adapters/tools_catalog.py — tool definitions
[ ] 6. application/use_cases/investigate.py — (opcional) tasks automáticas
[ ] 7. infrastructure/prompts/<nome>-agent-prompt.md — documentação LLM
[ ] 8. infrastructure/prompts/orchestrator-prompt.md — atualizar fontes
[ ] 9. Testar: /health do MCP → /chat com tool call → /investigate
```

---

## Exemplo completo: Splunk

| Passo | Arquivo | O que foi feito |
|-------|---------|-----------------|
| 1 | `application/ports/log_source.py` | Protocol `LogSource` com `search_logs`, `find_error_patterns` |
| 2 | `infrastructure/adapters/splunk_log_adapter.py` | Implementação via MCPClient → Splunk MCP |
| 3 | `infrastructure/config.py` | `"splunk": MCPServerConfig(endpoint=os.getenv("SPLUNK_MCP_ENDPOINT", ...))` |
| 4 | `api/dependencies.py` | `_splunk_client()`, `SPLUNK_TOOLS`, routing com strip de prefixo |
| 5 | `infrastructure/adapters/tools_catalog.py` | 3 tools: `splunk_search`, `splunk_errors`, `splunk_patterns` |
| 6 | — | Não integrado no /investigate (apenas /chat por enquanto) |
| 7 | `infrastructure/prompts/splunk-agent-prompt.md` | (pendente) |
| 8 | `infrastructure/prompts/orchestrator-prompt.md` | Splunk listado como fonte ativa |

---

## Comparação: versão atual vs hexagonal

| Aspecto | Versão atual (`orchestrator/`) | Versão hexagonal |
|---------|-------------------------------|------------------|
| Onde registrar endpoint | `config.py` | `infrastructure/config.py` (igual) |
| Onde rotear tools | `orchestrator.py` → `_execute_tool` (função monolítica) | `api/dependencies.py` → `execute_tool` (set + elif) |
| Onde adicionar tool definitions | `llm_client.py` → `AVAILABLE_TOOLS` (inline) | `infrastructure/adapters/tools_catalog.py` (arquivo separado) |
| Onde criar agent para /investigate | `agents/<nome>.py` (classe com MCPClient) | `infrastructure/adapters/<nome>_adapter.py` (implementa Protocol) |
| Onde ligar no /investigate | `orchestrator.py` → `_gather_signals` (inline) | `application/use_cases/investigate.py` (injetado via DI) |
| Testabilidade | Precisa mockar MCPClient | Injeta adapter fake no construtor do use case |

**Benefício principal**: na hexagonal, para testar o `InvestigateUseCase` com
um novo MCP, basta criar um adapter fake (in-memory) que implementa o Protocol.
Não precisa subir HTTP, não precisa de MCP server rodando.

---

## FAQ

**P: Preciso criar um MCP server novo (em `mcp-servers/`) também?**
R: Sim, se a fonte não tem um MCP server pronto. Siga o padrão de
`mcp-servers/splunk.py` ou `mcp-servers/logs_parquet.py`. O MCP server
é um processo separado — o orchestrator só fala com ele via HTTP.

**P: E se o MCP server já existe (ex: Go binary do VictoriaMetrics)?**
R: Não precisa criar nada em `mcp-servers/`. Apenas registre o endpoint
no `config.py` e crie o adapter que fala com ele via `MCPClient`.

**P: Posso usar JSON-RPC nativo em vez de REST `/tools/{name}`?**
R: Sim. O `MCPClient` detecta automaticamente: se o endpoint termina em
`/mcp` ou `/api/mcp`, usa JSON-RPC. Caso contrário, usa REST com fallback.

**P: E se o MCP server não segue nenhum dos dois protocolos?**
R: Crie um adapter que fala direto com a API (sem MCPClient). Exemplo:
`TempoTraceAdapter` quando Tempo está em modo nativo (porta 3100) — o
`MCPClient` detecta e chama `_call_tempo_native()` internamente.

**P: Preciso prefixar os nomes das tools?**
R: Sim, quando o nome é genérico (`search`, `query`). Prefixe com o nome
do MCP (`splunk_search`, `logs_search`). Se o nome já é único
(`find_firing_alerts`, `get_incident`), não precisa.

**P: Quantos MCPs posso adicionar?**
R: Sem limite técnico. Cada MCP é um pod independente. O orchestrator
roteia via sets no `execute_tool`. Performance depende da latência de
cada MCP, não da quantidade.
