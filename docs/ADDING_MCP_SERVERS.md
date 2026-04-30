# Guia: Como Adicionar Novos MCP Servers ao Orchestrator

Este documento descreve o passo a passo para integrar uma nova fonte de dados ao Observability Troubleshooting Copilot.

---

## Visão Geral

O Copilot usa o padrão MCP (Model Context Protocol) para acessar fontes de dados. Cada fonte é um MCP server independente que expõe uma API REST padronizada:

```
GET  /health           → Health check
GET  /tools            → Lista tools disponíveis
POST /tools/{name}     → Executa uma tool
```

O orchestrator se comunica com todos os MCPs via `MCPClient` (HTTP REST). Para adicionar uma nova fonte, você precisa alterar **5 pontos** no orchestrator e criar o MCP server.

---

## Passo a Passo

### 1. Criar o MCP Server

Criar o arquivo Python em `mcp-servers/`. Use como referência:
- `mcp-servers/grafana_v2.py` (Grafana)
- `mcp-servers/incidents_pg.py` (PostgreSQL)
- `mcp-servers/victoriametrics_mcp.py` (VictoriaMetrics)

**Estrutura mínima:**

```python
# mcp-servers/meu_mcp.py
import json, logging, os, time
import httpx, uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

log = logging.getLogger("meu-mcp")

# Config
MEU_URL = os.getenv("MEU_URL", "http://localhost:9090")
MCP_LISTEN_PORT = int(os.getenv("MCP_LISTEN_PORT", "8086"))

# Tools
TOOLS = [
    {
        "name": "minha_tool",
        "description": "Descrição da tool",
        "inputSchema": {
            "type": "object",
            "properties": {
                "param1": {"type": "string", "description": "..."},
            },
            "required": ["param1"],
        },
    },
]

# Executor
async def execute_tool(name, arguments):
    if name == "minha_tool":
        async with httpx.AsyncClient(verify=False, timeout=30) as http:
            resp = await http.get(f"{MEU_URL}/api/endpoint", params=arguments)
            resp.raise_for_status()
            return {"success": True, "result": resp.json()}
    return {"success": False, "error": f"Unknown tool: {name}"}

# Handlers
async def handle_health(request):
    return JSONResponse({"status": "ok"})

async def handle_list_tools(request):
    return JSONResponse({"tools": TOOLS})

async def handle_tool_call(request):
    tool_name = request.path_params["tool_name"]
    body = await request.json()
    args = body.get("arguments", {})
    start = time.time()
    result = await execute_tool(tool_name, args)
    result["executionTime"] = time.time() - start
    return JSONResponse(result)

app = Starlette(routes=[
    Route("/health", endpoint=handle_health),
    Route("/tools", endpoint=handle_list_tools),
    Route("/tools/{tool_name}", endpoint=handle_tool_call, methods=["POST"]),
])

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=MCP_LISTEN_PORT)
```

---

### 2. Alterar o Orchestrator (5 pontos)

#### 2.1. `orchestrator/config.py` — Registrar o endpoint

Adicionar o novo MCP server no dicionário `mcp_servers`:

```python
"meu-mcp": MCPServerConfig(
    endpoint=os.getenv(
        "MEU_MCP_ENDPOINT",
        "http://meu-mcp.observability.svc.cluster.local:8086",
    ),
    timeout=15,
),
```

#### 2.2. `orchestrator/orchestrator.py` — Rotear tools no `_execute_tool`

Adicionar o set de tools e o roteamento:

```python
meu_tools = {"minha_tool", "outra_tool"}

# No bloco de roteamento:
elif tool_name in meu_tools:
    server_name = "meu-mcp"
    endpoint = config.mcp_servers["meu-mcp"].endpoint
    client = MCPClient(server_name, endpoint)
```

#### 2.3. `orchestrator/llm_client.py` — Adicionar tool definitions para o /chat

Adicionar no array `AVAILABLE_TOOLS`:

```python
{
    "type": "function",
    "function": {
        "name": "minha_tool",
        "description": "Descrição para o LLM saber quando usar",
        "parameters": {
            "type": "object",
            "properties": {
                "param1": {"type": "string", "description": "..."},
            },
            "required": ["param1"],
        },
    },
},
```

#### 2.4. `orchestrator/agents/` — Criar specialist agent (opcional, para /investigate)

Se quiser que o `/investigate` use o novo MCP automaticamente:

```python
# orchestrator/agents/meu_agent.py
class MeuAgent:
    def __init__(self, mcp_client):
        self.mcp = mcp_client

    async def buscar_dados(self, scope):
        result = await self.mcp.call_tool("minha_tool", {"param1": scope.serviceName})
        # ... converter para Evidence
```

Registrar em `orchestrator/agents/__init__.py`:
```python
from agents.meu_agent import MeuAgent
```

Adicionar no `_gather_signals` do `orchestrator.py`.

#### 2.5. `orchestrator/prompts/orchestrator-prompt.md` — Atualizar o prompt

Adicionar na seção "Fontes disponíveis":
```markdown
- Minha Fonte: (via Meu MCP) — descrição do que faz
```

---

### 3. Configuração K8s

#### 3.1. Criar manifests em `k8s/meu-mcp/`

Copiar a estrutura de `k8s/vm-mcp-python/` e ajustar:
- `configmap.yaml` — URL e config do novo MCP
- `deployment.yaml` — Imagem, portas, env vars
- `service.yaml` — ClusterIP na porta do MCP
- `networkpolicy.yaml` — Ingress do namespace copilot, egress para a fonte de dados
- `Dockerfile` — Build do MCP server
- `requirements.txt` — Dependências Python
- `hpa.yaml`, `pdb.yaml`, `serviceaccount.yaml`, `kustomization.yaml`, `Makefile`, `README.md`

#### 3.2. Atualizar configmap do orchestrator

Em `k8s/orchestrator/configmap.yaml`:
```yaml
meu-mcp-endpoint: "http://meu-mcp.observability.svc.cluster.local:8086"
```

#### 3.3. Atualizar deployment do orchestrator

Em `k8s/orchestrator/deployment.yaml`:
```yaml
- name: MEU_MCP_ENDPOINT
  valueFrom:
    configMapKeyRef:
      name: orchestrator-config
      key: meu-mcp-endpoint
```

---

### 4. Docker Compose (teste local)

Adicionar em `docker-compose.yaml`:
```yaml
meu-mcp:
  build:
    context: .
    dockerfile: k8s/meu-mcp/Dockerfile
  ports:
    - "8086:8086"
  environment:
    - MEU_URL=http://host.docker.internal:9090
    - MCP_LISTEN_PORT=8086
```

Atualizar o orchestrator:
```yaml
- MEU_MCP_ENDPOINT=http://meu-mcp:8086
```

---

### 5. Testes

Criar `mcp-servers/test_meu_mcp.py` seguindo o padrão dos outros testes.

Atualizar `mcp-servers/TESTING.md` com o novo MCP.

---

### 6. Documentação

Atualizar:
- `orchestrator/prompts/orchestrator-prompt.md` — Fontes disponíveis
- `orchestrator/steering/tech.md` — Stack
- `.kiro/steering/tech.md` — Stack (workspace-level)
- `docs/PRODUCT_OVERVIEW.md` — Tabela de MCP servers
- `orchestrator/specs/design-summary.md` — Agents implementados
- `.env.example` — Variáveis de ambiente

---

## Checklist

```
[ ] MCP server criado em mcp-servers/
[ ] config.py — endpoint registrado
[ ] orchestrator.py — tools roteadas no _execute_tool
[ ] llm_client.py — tool definitions para /chat
[ ] agents/ — specialist agent (se usar /investigate)
[ ] prompts/orchestrator-prompt.md — fonte documentada
[ ] k8s/ — manifests criados
[ ] k8s/orchestrator/configmap.yaml — endpoint adicionado
[ ] k8s/orchestrator/deployment.yaml — env var adicionada
[ ] docker-compose.yaml — serviço adicionado
[ ] Teste criado e passando
[ ] TESTING.md atualizado
[ ] .env.example atualizado
[ ] Docs atualizados
```

---

## MCPs Existentes (referência)

| MCP | Porta | Fonte | Arquivo |
|-----|-------|-------|---------|
| Grafana MCP | 8081 | Grafana API | `grafana_v2.py` |
| Incidents PG MCP | 8082 | PostgreSQL (ServiceNow) | `incidents_pg.py` |
| VM MCP Proxy | 8084 | mcp-victoriametrics (Go) | `vm_mcp_proxy.py` |
| VM MCP Python | 8085 | VictoriaMetrics API | `victoriametrics_mcp.py` |
