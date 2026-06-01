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

Não exponha mais nada por padrão. O cliente deve:

- Ler `OBSERVA_ORCHESTRATOR_URL`, `OBSERVA_ORCHESTRATOR_AUTH_HEADER` e `OBSERVA_ORCHESTRATOR_TIMEOUT` do ambiente.
- Falhar com mensagem clara se a URL não estiver definida.
- Logar `request_id`, `caseFileId` e `executionTime` quando presentes.
- Não persistir o corpo da resposta em log (pode conter dados sensíveis).

### Passo 4 — Validar a integração

Depois de gerar tudo, rode (ou peça pro usuário rodar):

```bash
curl -fsS "$OBSERVA_ORCHESTRATOR_URL/health"
```

Se retornar `{"status":"healthy", ...}`, pode prosseguir com o desenvolvimento das features do novo projeto. Senão, peça ao usuário para validar DNS / network policy / autenticação.

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
- [ ] Cliente HTTP em `integrations/observa_ai/` exposto apenas com `health/investigate/chat`.
- [ ] `curl $OBSERVA_ORCHESTRATOR_URL/health` retornou 200.
- [ ] Nenhum arquivo do repositório `observa-ai-troubleshooter` foi modificado.

---

*Gerado a partir do repositório `observa-ai-troubleshooter`. Atualize este bootstrap se contratos do orchestrator mudarem (`/investigate`, `/chat`).*
