# Observability Troubleshooting Copilot

## Visão Executiva

O Observability Troubleshooting Copilot é uma plataforma de triagem inteligente de incidentes que **correlaciona automaticamente** métricas, alertas, incidentes e traces de múltiplas fontes para acelerar a identificação de causa raiz e reduzir o MTTR.

O sistema foi projetado para ser **agnóstico a ferramentas de borda** — qualquer frontend (bot do Teams, AWS DevOps Agent, portal web, CLI, Slack) pode consumir a mesma API. A inteligência está no backend, não no cliente.

---

## Problema que Resolve

### Cenário atual (sem o Copilot)

Quando um alerta dispara ou um incidente é aberto, o engenheiro de plantão precisa:

1. Abrir o Grafana para ver o alerta
2. Abrir o ServiceNow para ver o incidente
3. Abrir o VictoriaMetrics para consultar métricas
4. Abrir o Splunk para buscar logs
5. Abrir o Tempo para rastrear traces
6. Manualmente correlacionar informações entre 5+ ferramentas
7. Identificar qual serviço está afetado
8. Formular hipóteses
9. Decidir próximos passos

**Tempo médio**: 15-45 minutos só para triagem inicial.
**Problema**: Troca de contexto constante entre ferramentas, informação fragmentada, dependência de conhecimento tribal.

### Com o Copilot

O engenheiro faz UMA pergunta:

```
"Quais alertas e incidentes estão abertos para grafana-tempo?"
```

E recebe em **segundos** um relatório completo com:
- Alertas firing correlacionados
- Incidentes relacionados agrupados por padrão
- Métricas de golden signals (latência, erros, throughput, saturação)
- Links diretos para dashboards, painéis e KB articles
- Hipóteses rankeadas por confiança
- Próximos passos seguros (read-only)

**Tempo médio**: 30-60 segundos para triagem completa.

---

## Arquitetura

### Princípio: Agnóstico a Ferramentas de Borda

O Copilot expõe duas APIs REST que qualquer cliente pode consumir:

```
┌─────────────────────────────────────────────────────────┐
│                    FRONTENDS (Borda)                      │
│                                                          │
│   Bot Teams  │  AWS DevOps Agent  │  Portal Web  │  CLI │
│              │                    │              │       │
└──────┬───────┴────────┬───────────┴──────┬───────┴──────┘
       │                │                  │
       ▼                ▼                  ▼
┌─────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR                          │
│                                                          │
│   POST /investigate  →  Análise estruturada (JSON)      │
│   POST /chat         →  Análise conversacional (LLM)    │
│   GET  /health       →  Health check                    │
│   GET  /metrics      →  Métricas Prometheus              │
│                                                          │
└──────┬───────────────┬───────────────┬──────────────────┘
       │               │               │
       ▼               ▼               ▼
┌────────────┐  ┌────────────┐  ┌────────────────┐
│  Grafana   │  │ Incidents  │  │ VictoriaMetrics│
│  MCP       │  │ PG MCP     │  │ MCP Proxy      │
└─────┬──────┘  └─────┬──────┘  └───────┬────────┘
      │               │                 │
   Grafana API    PostgreSQL      VictoriaMetrics
                  (ServiceNow)
```

### Dois modos de operação

| Endpoint | Usa LLM? | Custo de tokens | Caso de uso |
|----------|----------|-----------------|-------------|
| `POST /investigate` | **Não** | Zero | Análise estruturada automática. Busca alertas, incidentes e métricas em paralelo. Retorna JSON com evidências, hipóteses e próximos passos. Ideal para integrações programáticas. |
| `POST /chat` | **Sim** | Tokens por request | Análise conversacional. O LLM decide quais tools chamar, correlaciona dados e gera relatório em linguagem natural. Ideal para interação humana. |

**Ponto chave**: O `/investigate` faz a coleta e correlação de dados **sem gastar tokens**. O LLM só é usado no `/chat` quando o usuário precisa de análise em linguagem natural. Isso permite que integrações automatizadas (bots, pipelines, alertas) usem o `/investigate` com custo zero de LLM.

### Padrão MCP (Model Context Protocol)

Cada fonte de dados é acessada via um MCP server independente:

| MCP Server | Linguagem | Fonte de Dados | Modo |
|------------|-----------|----------------|------|
| Grafana MCP | Python | Grafana API (alertas, dashboards) | SSE + REST |
| Incidents PG MCP | Python | PostgreSQL / ServiceNow | SSE + REST |
| VM MCP Proxy | Python | VictoriaMetrics (via Go binary) | REST → MCP SSE |
| VictoriaMetrics MCP | Go | VictoriaMetrics API (PromQL) | SSE / HTTP |

**Benefício**: Adicionar uma nova fonte de dados = criar um novo MCP server. O orchestrator não muda.

---

## Capacidades

### 1. Coleta Automática de Sinais (sem LLM)

O `/investigate` coleta dados de todas as fontes em paralelo, sem usar LLM:

- **Alertas Grafana**: alertas firing filtrados por `application_service`, `business_capability`, etc.
- **Incidentes ServiceNow**: busca por labels do Grafana no campo `description` (prioridade) com fallback para `cmdb_ci_name`
- **Métricas VictoriaMetrics**: golden signals (latência, erros, throughput, saturação) via catálogo de queries pré-definidas
- **Expressão do alerta**: quando o input é um Alert UID, executa a expressão PromQL do alerta contra o VictoriaMetrics

### 2. Correlação Inteligente

Chave canônica: `application_service`

O sistema normaliza labels de diferentes fontes para um vocabulário comum:

| Fonte | Campo original | Label canônica |
|-------|---------------|----------------|
| Grafana | `application_service` | `application_service` |
| ServiceNow | `description` (labels do Grafana) | `application_service` |
| ServiceNow | `cmdb_ci_name` (fallback) | `application_service` |
| Grafana | `owner_squad` | `owner_squad` |
| ServiceNow | `assignment_group_name` | `owner_squad` |

Hierarquia de negócio:
```
business_capability → business_domain → business_service → application_service
```

### 3. Knowledge Base Integrada

Alertas Grafana contêm referências a artigos KB do ServiceNow nas annotations.
O sistema extrai automaticamente o KB ID e monta o link direto:

```
📖 KB: KB0001718 → https://servicenow.corp/nav_to.do?uri=%2Fkb_view.do%3Fsysparm_article%3DKB0001718
```

### 4. Golden Signals Automáticos

Catálogo de queries PromQL pré-definidas executadas automaticamente:

| Categoria | Queries |
|-----------|---------|
| Latência | P99, P95, média |
| Tráfego | Request rate, rate por status code |
| Erros | Error rate 5xx, error count |
| Saturação | CPU, memória, memória vs limite |
| Infraestrutura | Pod restarts, pod status, réplicas |

O catálogo é editável sem mexer em código (`orchestrator/steering/metrics-catalog.md`).

### 5. Hipóteses Rankeadas

O sistema gera hipóteses com confidence score baseado em:
- Quantidade de evidências correlacionadas
- Match por `application_service` entre fontes
- Match por `fingerprint` (correlação precisa alerta ↔ incidente)
- Presença de alertas firing

### 6. Guardrails de Segurança

| Guardrail | Descrição |
|-----------|-----------|
| Read-only | Nenhuma ação de escrita permitida |
| PII redaction | Emails, IPs, telefones, API keys redijidos automaticamente |
| Evidence-based | Toda afirmação tem query/resultado/link como evidência |
| Audit trail | Toda investigação é logada com timestamp e detalhes |

---

## Benefícios

### Para SRE / On-call

| Antes | Depois |
|-------|--------|
| 15-45 min para triagem inicial | 30-60 segundos |
| 5+ ferramentas abertas simultaneamente | 1 pergunta, 1 resposta completa |
| Correlação manual entre fontes | Correlação automática por `application_service` |
| Conhecimento tribal necessário | KB articles linkados automaticamente |
| Sem rastreabilidade | CaseFile com audit trail completo |

### Para Gestão / Arquitetura

| Benefício | Detalhe |
|-----------|---------|
| **Redução de MTTR** | Triagem automatizada reduz tempo de identificação de causa raiz |
| **Agnóstico a frontend** | Qualquer cliente (Teams, Slack, portal, CLI, AWS Agent) consome a mesma API |
| **Custo controlado de LLM** | `/investigate` não usa tokens. LLM só no `/chat` quando necessário |
| **Extensível** | Nova fonte de dados = novo MCP server. Orchestrator não muda |
| **Seguro** | Read-only, PII redaction, audit trail, sem ações de remediação automática |
| **Observável** | Métricas Prometheus (`observa_*`), logging estruturado, health checks |
| **Cloud-native** | Kubernetes-ready com HPA, PDB, NetworkPolicy, ServiceAccount |

### Para Times de Produto

| Benefício | Detalhe |
|-----------|---------|
| Consulta guiada sem acesso direto às ferramentas | O Copilot abstrai a complexidade |
| Relatórios em linguagem natural | `/chat` gera análises legíveis |
| Visibilidade cross-team | Hierarquia `business_capability → application_service` |

---

## Otimização de Custos: LLM vs Automático

Um dos princípios de design é **minimizar o uso de LLM** para operações que podem ser automatizadas:

### O que roda SEM LLM (custo zero de tokens)

| Operação | Endpoint | Como funciona |
|----------|----------|---------------|
| Buscar alertas firing | `/investigate` | GrafanaAgent consulta API diretamente |
| Buscar incidentes relacionados | `/investigate` | IncidentsAgent consulta PostgreSQL diretamente |
| Executar golden signals | `/investigate` | MetricsAgent executa catálogo de queries PromQL |
| Executar expressão do alerta | `/investigate` | MetricsAgent extrai PromQL do alerta e executa |
| Correlacionar sinais | `/investigate` | CorrelationEngine normaliza labels e agrupa |
| Gerar hipóteses | `/investigate` | HypothesisGenerator rankeia por confidence |
| Extrair KB link | `/investigate` | Grafana MCP parseia annotations automaticamente |
| PII redaction | Ambos | Guardrails aplicam regex em todos os resultados |
| Métricas Prometheus | `/metrics` | prometheus-client coleta automaticamente |

### O que usa LLM (custo de tokens)

| Operação | Endpoint | Quando usar |
|----------|----------|-------------|
| Análise conversacional | `/chat` | Quando o usuário precisa de relatório em linguagem natural |
| Decisão de quais tools chamar | `/chat` | LLM decide baseado na pergunta do usuário |
| Correlação semântica | `/chat` | Quando a correlação por labels não é suficiente |
| Sugestões contextuais | `/chat` | Próximos passos personalizados baseados no contexto |

**Estratégia recomendada**: Usar `/investigate` para coleta e correlação automática, e `/chat` apenas quando análise em linguagem natural é necessária.

---

## Stack Tecnológica

| Componente | Tecnologia | Justificativa |
|------------|-----------|---------------|
| Orchestrator | Python / FastAPI | Async, rápido, ecossistema rico |
| MCP Servers | Python (Grafana, Incidents) / Go (VictoriaMetrics) | MCP protocol nativo |
| Banco de dados | PostgreSQL (AWS RDS) | Incidentes ServiceNow |
| Métricas | VictoriaMetrics | PromQL compatível, alta performance |
| Alertas | Grafana Unified Alerting | Labels padronizadas |
| Incidentes | ServiceNow → PostgreSQL | Tabela `incidents_snow` |
| LLM | OpenAI GPT-4o (via gateway) | Function calling para tool use |
| Observabilidade | Prometheus metrics (`observa_*`) | Monitoramento do próprio sistema |
| Deploy | Kubernetes | HPA, PDB, NetworkPolicy |
| Containers | Docker | Build multi-stage |

---

## Extensibilidade

### Adicionar nova fonte de dados

1. Criar um MCP server (Python ou qualquer linguagem)
2. Expor endpoints REST: `GET /health`, `GET /tools`, `POST /tools/{name}`
3. Criar um specialist agent no orchestrator
4. Registrar no `config.py` e no `_execute_tool`
5. Adicionar tool definitions no `llm_client.py` para o `/chat`

**Nenhuma mudança no frontend necessária.**

### Adicionar novo frontend

1. Consumir `POST /investigate` (JSON estruturado) ou `POST /chat` (linguagem natural)
2. Implementar autenticação (futuro)
3. Renderizar a resposta no formato do frontend

**Nenhuma mudança no backend necessária.**

### Adicionar novas métricas ao catálogo

1. Editar `orchestrator/steering/metrics-catalog.md`
2. Adicionar blocos YAML com `name`, `category`, `query_template`
3. Reiniciar o orchestrator

**Nenhuma mudança em código necessária.**

---

## Roadmap

### Fase 1 — PoC (atual)
- ✅ Orchestrator com `/investigate` e `/chat`
- ✅ Grafana MCP (alertas, dashboards)
- ✅ Incidents PG MCP (ServiceNow → PostgreSQL)
- ✅ VictoriaMetrics MCP Proxy
- ✅ Correlação por `application_service`
- ✅ Golden signals automáticos
- ✅ KB links do ServiceNow
- ✅ Métricas Prometheus
- ✅ Logging estruturado
- ✅ Guardrails (PII, read-only, evidence-based)

### Fase 2 — Piloto
- Integração com frontend (Teams bot ou portal)
- Testes com usuários reais
- Ajuste de prompts baseado em feedback
- Rate limiting e circuit breaker
- CaseFile storage (PostgreSQL)
- CI/CD pipeline

### Fase 3 — Produção
- Logs: Splunk MCP
- Traces: Tempo MCP
- Auto-remediation (com aprovação humana)
- ML para ranking de hipóteses
- Feedback loop (usuários avaliam hipóteses)
- Multi-tenancy

---

## Métricas de Sucesso

| Métrica | Meta | Como medir |
|---------|------|------------|
| MTTR (Mean Time to Resolve) | Redução de 30% | Comparar antes/depois do Copilot |
| Tempo de triagem | < 2 minutos | `observa_investigation_duration_seconds` |
| Acurácia de hipóteses | > 70% | Feedback dos usuários |
| Adoção | > 50% dos on-calls usando | Contagem de sessões `/chat` |
| Troca de contexto | Redução de 80% | Pesquisa com usuários |
| Custo de LLM | < $X/mês | `observa_llm_tokens_total` |

---

## Segurança e Compliance

| Aspecto | Implementação |
|---------|---------------|
| Acesso a dados | Read-only em todas as fontes |
| PII | Redação automática (email, IP, telefone, API keys) |
| Secrets | Env vars, nunca commitados. K8s Secrets para deploy |
| Network | NetworkPolicy restritiva por namespace |
| Audit | Audit trail em cada CaseFile |
| Autenticação | Futuro (API key ou OAuth) |
| Autorização | Futuro (RBAC por team/capability) |

---

## Conclusão

O Observability Troubleshooting Copilot é uma plataforma que:

1. **Reduz MTTR** automatizando a triagem de incidentes
2. **É agnóstico a frontend** — qualquer cliente consome a mesma API
3. **Otimiza custos de LLM** — coleta e correlação automáticas sem tokens
4. **É extensível** — nova fonte = novo MCP server, novo frontend = consumir API
5. **É seguro** — read-only, PII redaction, audit trail
6. **É observável** — métricas Prometheus, logging estruturado

O sistema está pronto para **piloto controlado** com usuários reais.
