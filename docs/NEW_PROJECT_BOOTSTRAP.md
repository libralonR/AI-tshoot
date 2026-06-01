# Bootstrap — Integrar um novo projeto com o Observa-AI Troubleshooter

> Copie **este arquivo** para a raiz do seu novo projeto (sugestão: `BOOTSTRAP_KIRO.md` ou `.kiro/steering/observa-ai-integration.md`) e abra a primeira sessão do Kiro pedindo:
>
> > **"Leia `BOOTSTRAP_KIRO.md` e siga as instruções para integrar este projeto ao Observa-AI Troubleshooter."**
>
> O Kiro vai conduzir o setup, criar os arquivos de configuração e guiar você nos campos a preencher. Ele **não** deve modificar o repositório `observa-ai-troubleshooter` — apenas consumi-lo via HTTP.

---

## 1. Contexto para o Kiro

Existe um serviço já implantado em Kubernetes chamado **Observa-AI Troubleshooter Orchestrator**. Ele é um copiloto de observabilidade que correlaciona métricas (VictoriaMetrics/PromQL), logs (Splunk/S3 Parquet), traces (Tempo), alertas (Grafana) e incidentes (ServiceNow → PostgreSQL) e devolve um *CaseFile* com hipóteses ranqueadas e evidências rastreáveis.

O orchestrator fala com fontes externas exclusivamente através de **MCP servers** (read-only). O novo projeto **não precisa** falar com os MCPs — o caminho recomendado é chamar o orchestrator via HTTP usando o DNS interno já provisionado no cluster.

### Princípios não-negociáveis (herdados do projeto base)

- **Read-only**: nenhuma chamada deve mutar dados em Grafana, ServiceNow, VictoriaMetrics, Splunk, Tempo ou no PostgreSQL de incidentes.
- **Sem PII em outputs**: redigir quando necessário antes de exibir/persistir.
- **Toda afirmação precisa ter evidência**: query, traceId, link de dashboard, número de incidente, etc.
- **Chave canônica de correlação**: `application_service`. Hierarquia: `business_capability → business_domain → business_service → application_service`.
- **Nenhum segredo em código**: tokens vão em `.env` / Kubernetes Secret, referenciados por variável de ambiente.

---

## 2. O que o Kiro deve fazer ao ler este arquivo

Execute exatamente nesta ordem:

### Passo 1 — Coletar dados do ambiente (perguntar ao usuário)

Pergunte ao usuário e **não prossiga** sem respostas:

1. `ORCHESTRATOR_BASE_URL` — DNS do orchestrator (ex.: `https://observa-ai.internal.empresa.com` ou `http://orchestrator.copilot.svc.cluster.local:8080`).
2. `ORCHESTRATOR_AUTH` — se houver autenticação na frente (Bearer token, API key, mTLS, ou nenhuma).
3. **Modo de uso pretendido** — uma destas opções:
   - `orchestrator-only` (recomendado): só consumir `/investigate` e `/chat`.
   - `orchestrator+mcp-direct`: além do orchestrator, falar SSE direto com algum MCP específico (precisa do DNS de cada MCP e dos tokens correspondentes).
4. Linguagem/stack do novo projeto (Python, Node, Go, etc.) — para gerar exemplos no idioma certo.
5. Caso o modo seja `orchestrator+mcp-direct`, perguntar quais MCPs serão usados (Grafana, Incidents PG, VictoriaMetrics) e coletar os endpoints/credenciais.

### Passo 2 — Criar os arquivos de configuração no novo projeto

Crie **três arquivos** na raiz do novo projeto (preencha apenas o que o usuário informou; deixe placeholders para o resto):

#### 2.1 `.env.example`

```dotenv
# ============================================================
# Observa-AI Orchestrator (caminho recomendado)
# ============================================================
OBSERVA_ORCHESTRATOR_URL=https://CHANGE-ME.internal.empresa.com
OBSERVA_ORCHESTRATOR_TIMEOUT=120
OBSERVA_ORCHESTRATOR_AUTH_HEADER=
# Exemplos: "Authorization: Bearer xxx" | "X-API-Key: xxx" | vazio se não houver auth

# Identificação do chamador (vai no campo "user" das requisições)
OBSERVA_CALLER=novo-projeto

# ============================================================
# (Opcional) MCP servers diretos — só preencher se o modo for
# orchestrator+mcp-direct. Caso contrário, deixe vazio.
# ============================================================
GRAFANA_MCP_ENDPOINT=
INCIDENTS_PG_MCP_ENDPOINT=
VM_MCP_ENDPOINT=

# Tokens só são necessários se for falar SSE direto (não recomendado).
GRAFANA_TOKEN=
```

#### 2.2 `.kiro/integration.yaml`

```yaml
# Configuração de integração com o Observa-AI Troubleshooter.
# Editado pelo Kiro a partir das respostas do usuário.

mode: orchestrator-only       # ou orchestrator+mcp-direct
orchestrator:
  base_url_env: OBSERVA_ORCHESTRATOR_URL
  auth_header_env: OBSERVA_ORCHESTRATOR_AUTH_HEADER
  timeout_env: OBSERVA_ORCHESTRATOR_TIMEOUT
  endpoints:
    health:      GET  /health
    investigate: POST /investigate
    chat:        POST /chat
    metrics:     GET  /metrics
    steering:    GET  /steering

# Convenções do orchestrator que o novo projeto deve respeitar
conventions:
  canonical_correlation_key: application_service
  business_hierarchy:
    - business_capability
    - business_domain
    - business_service
    - application_service
  read_only: true
  redact_pii: true

# Só preenchido quando mode == orchestrator+mcp-direct
mcp_direct:
  grafana:
    endpoint_env: GRAFANA_MCP_ENDPOINT
    transport: sse        # SSE em /sse, REST de fallback em /tools/{tool}
    token_env: GRAFANA_TOKEN
  incidents_pg:
    endpoint_env: INCIDENTS_PG_MCP_ENDPOINT
    transport: sse
  victoriametrics:
    endpoint_env: VM_MCP_ENDPOINT
    transport: http       # via vm-mcp-proxy → /tools/{tool}
```

#### 2.3 `.kiro/steering/observa-ai.md`

```markdown
---
inclusion: always
---
# Integração com Observa-AI Troubleshooter

Este projeto consome o orchestrator do Observa-AI via HTTP. Use o cliente em
`integrations/observa_ai/` (gerado pelo Kiro) ao invés de falar direto com
qualquer MCP, salvo se `mode: orchestrator+mcp-direct` em `.kiro/integration.yaml`.

## Regras
- NUNCA comitar tokens. Use as variáveis de ambiente listadas em `.env.example`.
- Toda chamada que vai para o usuário deve carregar evidência (queries, traceIds, links).
- Chave de correlação: `application_service`. Quando o input tiver `cmdb_ci_name`
  ou `service.name`, normalize antes de mandar para o orchestrator.
- Read-only: nunca chamar endpoint que não seja `/health`, `/investigate`,
  `/chat`, `/metrics`, `/steering`, `/casefile/{id}`.
```

### Passo 3 — Gerar o cliente HTTP mínimo

Em `integrations/observa_ai/`, gere um cliente na linguagem que o usuário escolheu, expondo apenas:

- `health()` → `GET /health`
- `investigate(input_type, value, user, filters=None)` → `POST /investigate`
- `chat(message, session_id=None)` → `POST /chat`

Esse cliente é para uso **dentro do código** do novo projeto (jobs, APIs, scripts).
Para uso **conversacional dentro do Kiro**, o caminho é o MCP server do Passo 4 — não
duplique lógica entre os dois; o MCP wrapper já fala com o orchestrator pela mesma rota
HTTP.

Não exponha mais nada por padrão. O cliente deve:

- Ler `OBSERVA_ORCHESTRATOR_URL`, `OBSERVA_ORCHESTRATOR_AUTH_HEADER` e `OBSERVA_ORCHESTRATOR_TIMEOUT` do ambiente.
- Falhar com mensagem clara se a URL não estiver definida.
- Logar `request_id`, `caseFileId` e `executionTime` quando presentes.
- Não persistir o corpo da resposta em log (pode conter dados sensíveis).

### Passo 4 — Tornar o orchestrator acessível **dentro do próprio Kiro** (sem UI)

Objetivo: o agente do Kiro, durante uma conversa, deve poder chamar
`investigate`, `chat` e `health` no orchestrator como ferramentas nativas — sem
abrir terminal, sem UI custom. A forma mais limpa é um **MCP server local**
que envelopa o HTTP do orchestrator e é registrado em `.kiro/settings/mcp.json`.
Junto disso, uma **Skill** orienta o agente sobre quando usar; e um **Hook**
opcional dá disparo manual com um clique.

#### 4.1 — Crie o MCP server local: `tools/observa_ai_mcp.py`

Arquivo único, autocontido (PEP 723 declara as deps; `uvx` instala on demand):

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.2.0", "httpx>=0.27.0"]
# ///
"""Observa-AI Troubleshooter — MCP wrapper sobre o orchestrator HTTP.

Expõe três tools no Kiro:
  - observa_health
  - observa_investigate
  - observa_chat

Lê endpoint/auth de variáveis de ambiente para nunca hardcodar segredo.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

ORCHESTRATOR_URL = os.environ.get("OBSERVA_ORCHESTRATOR_URL", "").rstrip("/")
AUTH_HEADER_RAW = os.environ.get("OBSERVA_ORCHESTRATOR_AUTH_HEADER", "").strip()
TIMEOUT = float(os.environ.get("OBSERVA_ORCHESTRATOR_TIMEOUT", "120"))
CALLER = os.environ.get("OBSERVA_CALLER", "kiro-agent")

if not ORCHESTRATOR_URL:
    raise RuntimeError(
        "OBSERVA_ORCHESTRATOR_URL não definido. Configure no .env ou no "
        "campo `env` do .kiro/settings/mcp.json."
    )


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json", "X-Caller": CALLER}
    if AUTH_HEADER_RAW and ":" in AUTH_HEADER_RAW:
        name, _, value = AUTH_HEADER_RAW.partition(":")
        headers[name.strip()] = value.strip()
    return headers


mcp = FastMCP("observa-ai")


@mcp.tool()
async def observa_health() -> dict[str, Any]:
    """Health check do orchestrator. Use antes de uma investigação se houver dúvida de conectividade."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{ORCHESTRATOR_URL}/health", headers=_headers())
        r.raise_for_status()
        return r.json()


@mcp.tool()
async def observa_investigate(
    input_type: str,
    value: str,
    user: str | None = None,
    filters: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Dispara uma investigação no Observa-AI.

    Args:
        input_type: 'INCIDENT_ID' | 'ALERT_UID' | 'SYMPTOM'.
        value: ID/UID ou sintoma em texto livre.
        user: Quem está pedindo (default: OBSERVA_CALLER).
        filters: Opcional. Chaves: application_service, owner_squad, severidade,
                 business_capability, grafana_folder.

    Returns:
        CaseFile resumido com scope, evidence, hypotheses, correlationGaps.
    """
    payload = {"input_type": input_type, "value": value, "user": user or CALLER}
    if filters:
        payload["filters"] = filters
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(
            f"{ORCHESTRATOR_URL}/investigate", headers=_headers(), json=payload
        )
        r.raise_for_status()
        return r.json()


@mcp.tool()
async def observa_chat(message: str, session_id: str | None = None) -> dict[str, Any]:
    """Conversa com o copilot do Observa-AI. Reenvie session_id para preservar contexto."""
    payload: dict[str, Any] = {"message": message}
    if session_id:
        payload["session_id"] = session_id
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(
            f"{ORCHESTRATOR_URL}/chat", headers=_headers(), json=payload
        )
        r.raise_for_status()
        return r.json()


if __name__ == "__main__":
    mcp.run()
```

#### 4.2 — Registre em `.kiro/settings/mcp.json`

Crie ou faça merge no arquivo `.kiro/settings/mcp.json` do novo projeto:

```json
{
  "mcpServers": {
    "observa-ai": {
      "command": "uvx",
      "args": [
        "--from", "mcp[cli]",
        "--with", "httpx",
        "python", "tools/observa_ai_mcp.py"
      ],
      "env": {
        "OBSERVA_ORCHESTRATOR_URL": "${OBSERVA_ORCHESTRATOR_URL}",
        "OBSERVA_ORCHESTRATOR_AUTH_HEADER": "${OBSERVA_ORCHESTRATOR_AUTH_HEADER}",
        "OBSERVA_ORCHESTRATOR_TIMEOUT": "${OBSERVA_ORCHESTRATOR_TIMEOUT:-120}",
        "OBSERVA_CALLER": "${OBSERVA_CALLER:-kiro-agent}"
      },
      "disabled": false,
      "autoApprove": ["observa_health"]
    }
  }
}
```

Notas:

- `uvx` cuida de instalar `mcp` e `httpx` em sandbox — não polui o projeto.
- `autoApprove` libera só o `observa_health`. `observa_investigate` e `observa_chat` continuam pedindo confirmação na primeira chamada (intencional — read-only mas afeta produção).
- Se o cluster precisar de variáveis extras (proxy, CA bundle), adicione no bloco `env`.

Depois de salvar, reconecte o servidor pelo painel "MCP Servers" do Kiro **ou** simplesmente abra um novo chat — o Kiro recarrega a config sozinho.

#### 4.3 — Crie a Skill que ensina o agente a usar

Arquivo: `.kiro/skills/observa-ai-troubleshooter.md`

```markdown
---
name: observa-ai-troubleshooter
description: |
  Use quando o usuário precisar investigar incidente, alerta ou sintoma de produção
  (latência, 5xx, queda de serviço, etc.). Aciona o orchestrator do Observa-AI via
  MCP para correlacionar métricas, logs, traces, alertas e incidentes.
---

# Observa-AI Troubleshooter

## Quando usar

Acione esta skill se a mensagem do usuário contiver QUALQUER um dos seguintes:

- ID de incidente do ServiceNow (formato `INC` + dígitos, ex: `INC0012345`).
- UID de alerta do Grafana.
- Descrição de sintoma de produção (ex: "checkout-api lento", "5xx no auth-service",
  "latência alta em pagamentos").
- Pergunta sobre causa raiz, MTTR, hipóteses, evidências de um incidente.

## Como decidir o `input_type`

| Padrão                                      | input_type    |
|---------------------------------------------|---------------|
| `^INC\d+$`                                  | `INCIDENT_ID` |
| UID alfanumérico do Grafana (alert rule)    | `ALERT_UID`   |
| Texto livre descrevendo o problema          | `SYMPTOM`     |

## Fluxo padrão

1. Se o usuário mencionou `application_service`, `owner_squad`, `business_capability`,
   `severidade` ou `grafana_folder`, monte o objeto `filters` e passe junto.
2. Chame `observa_investigate` (tool MCP do servidor `observa-ai`).
3. Apresente ao usuário, NESTA ORDEM:
   a) **Hipótese principal** com `confidence` e a hipótese de backup.
   b) **Evidências-chave**: queries PromQL, traceIds, números de incidente, links de dashboards.
   c) **Lacunas de correlação** (se houver) — sinalize quais labels faltaram.
   d) **Próximos passos read-only** — NUNCA sugira remediação automática.
4. Se o usuário quiser conversar mais sobre o resultado, use `observa_chat` reaproveitando
   o `session_id` retornado.

## Regras

- Toda hipótese exibida DEVE vir acompanhada de evidência (query, traceId ou link).
- NÃO invente queries — use só o que veio no campo `evidence` da resposta.
- NÃO ofereça botões/ações de remediação. A PoC é read-only.
- Redija qualquer PII que apareça em logs/descrições antes de ecoar pro usuário.
- Se `observa_health` falhar, peça ao usuário pra validar DNS/auth antes de tentar
  `observa_investigate`.

## Anti-exemplos

- ❌ Usuário diz "reinicia o pod" → você NÃO executa nem sugere comando de mutação.
- ❌ Resposta sem evidência ("acho que é o banco") → reformule citando a evidência.
- ❌ Chamar `observa_investigate` sem `application_service` para input do tipo SYMPTOM
  quando o usuário deu pistas claras — sempre extrai filtros do contexto.
```

#### 4.4 — (Opcional) Hook para disparo manual com um clique

Útil quando alguém cola um número de incidente no chat e quer investigar
sem digitar. Crie via comando `Open Kiro Hook UI` ou direto em
`.kiro/hooks/investigate-incident.kiro.hook`:

```json
{
  "name": "Investigate Incident",
  "version": "1.0.0",
  "description": "Dispara observa_investigate para o último INC mencionado no chat",
  "when": { "type": "userTriggered" },
  "then": {
    "type": "askAgent",
    "prompt": "Procure o último identificador no formato INCxxxxxxx mencionado nesta conversa e chame a tool observa_investigate com input_type=INCIDENT_ID e value=<o número encontrado>. Apresente o resultado seguindo a skill observa-ai-troubleshooter."
  }
}
```

### Passo 5 — Validar a integração

```bash
# 1. Conectividade direta (sem Kiro)
curl -fsS "$OBSERVA_ORCHESTRATOR_URL/health"
```

Esperado: `{"status":"healthy", ...}`.

```text
# 2. Dentro do Kiro, em um novo chat:
"Verifique a saúde do Observa-AI"
```

O agente deve invocar a tool `observa_health` automaticamente. Se ele perguntar
qual ferramenta usar, releia a skill — provavelmente a descrição precisa de mais
gatilhos.

```text
# 3. Teste end-to-end:
"Investigue o incidente INC0012345"
```

O agente deve chamar `observa_investigate` com `input_type=INCIDENT_ID` e
`value=INC0012345`, e devolver hipóteses com evidência.

Se algo falhar:

- **MCP server não aparece**: confira `.kiro/settings/mcp.json` e reconecte pelo painel "MCP Servers".
- **`uvx: command not found`**: instale `uv`/`uvx` (`brew install uv` ou ver https://docs.astral.sh/uv/getting-started/installation/).
- **401/403**: o `OBSERVA_ORCHESTRATOR_AUTH_HEADER` deve estar no formato `Header-Name: valor`. Sem dois pontos, é ignorado.
- **Timeout**: aumente `OBSERVA_ORCHESTRATOR_TIMEOUT` (default 120s).

---

## 3. Contratos do Orchestrator (referência rápida)

### `GET /health`

```json
{ "status": "healthy", "service": "orchestrator", "version": "1.1.0" }
```

### `POST /investigate`

Request:

```json
{
  "input_type": "INCIDENT_ID",
  "value": "INC0012345",
  "user": "novo-projeto",
  "filters": {
    "application_service": "checkout-api",
    "owner_squad": "squad-pagamentos"
  }
}
```

- `input_type` aceita `INCIDENT_ID`, `ALERT_UID` ou `SYMPTOM`.
- `value` é o ID/UID, ou um sintoma em texto livre (`"latência alta no checkout"`).
- `filters` é opcional. Chaves suportadas: `application_service`, `owner_squad`, `severidade`, `business_capability`, `grafana_folder`.

Response (resumo):

```json
{
  "caseFileId": "cf-...",
  "scope": { "application_service": "checkout-api", "...": "..." },
  "timeWindow": { "from": "...", "to": "..." },
  "evidence": [ { "id": "...", "type": "METRIC_ANOMALY", "...": "..." } ],
  "hypotheses": [ { "id": "...", "description": "...", "confidence": 0.82 } ],
  "correlationGaps": [ { "label": "namespace", "reason": "ausente no alerta" } ],
  "executionTime": 4.21
}
```

### `POST /chat`

Request:

```json
{ "message": "Por que o checkout-api está com erro 5xx?", "session_id": null }
```

Response:

```json
{ "response": "...", "session_id": "abc-123" }
```

Para preservar contexto multi-turno, reenvie o `session_id` retornado.

### `GET /metrics`

Endpoint Prometheus (formato texto). Use para scraping interno se quiser observar o consumo do novo projeto.

### `GET /steering`

Retorna o contexto de steering carregado pelo orchestrator (catálogos de queries, regras de correlação). Útil para debug — não dependa do conteúdo.

---

## 4. Modo `orchestrator+mcp-direct` (opcional)

Só use se houver justificativa clara (ex.: ferramenta interna de exploração que precisa de baixa latência ou de uma tool MCP específica). Os MCPs expõem dois transportes:

| Servidor              | Porta padrão (cluster) | Transport principal | Fallback REST              |
|-----------------------|------------------------|---------------------|----------------------------|
| Grafana MCP           | `8080`                 | SSE em `/sse`       | `POST /tools/{tool_name}`  |
| Incidents PG MCP      | `8080`                 | SSE em `/sse`       | `POST /tools/{tool_name}`  |
| VictoriaMetrics MCP   | `8084` (via proxy)     | HTTP REST           | `POST /tools/{tool_name}`  |

Liste tools disponíveis com `GET /tools`. Health check em `GET /health`.

**Atenção**: se o cluster tem network policies, o novo projeto provavelmente só tem rota até o orchestrator. Confirme antes de planejar esse modo.

---

## 5. Boas práticas para features do novo projeto

- **Antes de chamar `/investigate`**, normalize o input: se o usuário passar `service`, `service.name` ou `cmdb_ci_name`, mapeie para `application_service`.
- **Nunca** ofereça botões de "remediar/restart/rollback" baseados no resultado — a PoC é read-only e o usuário humano aprova qualquer ação.
- **Ao exibir hipóteses**, mostre sempre o `confidence` e os links/queries das evidências. Hipótese sem evidência não é hipótese.
- **Cache curto** (60–120s) das respostas do orchestrator é OK por `caseFileId`. Não cacheie por sintoma livre — janela temporal muda.
- **Timeouts**: `/investigate` pode demorar até ~120s em casos complexos. Configure timeout ≥ 120s no cliente HTTP.

---

## 6. Checklist final (o Kiro deve verificar antes de encerrar)

- [ ] `.env.example` criado com todas as variáveis necessárias e sem segredos reais.
- [ ] `.kiro/integration.yaml` preenchido com o `mode` escolhido.
- [ ] `.kiro/steering/observa-ai.md` em `inclusion: always`.
- [ ] `tools/observa_ai_mcp.py` criado e executável.
- [ ] `.kiro/settings/mcp.json` registrando o servidor `observa-ai` com as `env` apontando para as variáveis corretas.
- [ ] `.kiro/skills/observa-ai-troubleshooter.md` criada com gatilhos e regras.
- [ ] Cliente HTTP em `integrations/observa_ai/` exposto apenas com `health/investigate/chat` (uso direto pelo código do projeto, fora do Kiro).
- [ ] `curl $OBSERVA_ORCHESTRATOR_URL/health` retornou 200.
- [ ] Em um novo chat do Kiro, `observa_health` foi invocado com sucesso pelo agente.
- [ ] Nenhum arquivo do repositório `observa-ai-troubleshooter` foi modificado.

---

*Gerado a partir do repositório `observa-ai-troubleshooter`. Atualize este bootstrap se contratos do orchestrator mudarem (`/investigate`, `/chat`).*
