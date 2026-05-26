# Arquitetura Hexagonal — Visão Atual e Evolução

**Audiência**: Arquitetura, Gerência, Time de Engenharia
**Versão**: 1.0
**Status**: Proposta de evolução incremental

---

## Contexto

A plataforma **Observability Troubleshooting Copilot** já nasceu com princípios sólidos
de separação de responsabilidades. Esta proposta formaliza o que já temos e organiza
uma evolução natural em direção ao padrão **Hexagonal (Ports & Adapters)**, sem
refatorações disruptivas e preservando todo o valor já entregue.

A premissa: **70% do trabalho hexagonal já está feito.** A proposta cobre os 30%
restantes em fases pequenas e auditáveis.

---

## Estado Atual — Já Estamos Bem Posicionados

### Princípios hexagonais já presentes

| Princípio | Como já aplicamos hoje |
|-----------|------------------------|
| **Domínio separado** | `correlation.py`, `hypothesis.py`, `guardrails.py`, `models.py` são lógica pura, sem acoplamento a frameworks |
| **Múltiplos canais de entrada** | REST `/investigate`, REST `/chat`, UI Streamlit — todos usam o mesmo núcleo |
| **Múltiplos adapters por fonte** | VictoriaMetrics tem 3 implementações intercambiáveis (proxy SSE, MCP Python, MCP Go) |
| **Especialistas isolados** | `agents/grafana.py`, `agents/incidents.py`, `agents/metrics.py` cada um responsável por sua fonte |
| **Infraestrutura externalizada** | MCP servers rodam como processos independentes |

### Tradução visual do estado atual

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         ENTRADAS (já múltiplas)                          │
│                                                                          │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐    │
│   │  REST        │    │  REST        │    │  Streamlit UI         │    │
│   │  /investigate│    │  /chat       │    │  (aba chat/invest.)   │    │
│   └──────┬───────┘    └──────┬───────┘    └──────────┬───────────┘    │
│          │                   │                        │                 │
└──────────┼───────────────────┼────────────────────────┼─────────────────┘
           │                   │                        │
           ▼                   ▼                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      ORCHESTRATOR (núcleo + agentes)                     │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  DOMÍNIO (já isolado, lógica pura)                                │  │
│  │  • correlation.py  — correlação por application_service          │  │
│  │  • hypothesis.py   — geração e ranking de hipóteses              │  │
│  │  • guardrails.py   — PII redaction, read-only enforcement        │  │
│  │  • models.py       — Evidence, CaseFile, Hypothesis              │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  AGENTES (já isolam fontes externas)                              │  │
│  │  • GrafanaAgent    • IncidentsAgent    • MetricsAgent             │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└──────────────┬──────────────────┬─────────────────────┬─────────────────┘
               │                  │                     │
               ▼                  ▼                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    SAÍDAS — MCP Servers (já desacoplados)                │
│                                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  ┌──────────┐  ┌──────┐ │
│  │ Grafana  │  │ Incidents│  │ VictoriaMet. │  │  Tempo   │  │ LLM  │ │
│  │   MCP    │  │  PG MCP  │  │  3 opções    │  │   MCP    │  │ Gtwy │ │
│  └──────────┘  └──────────┘  └──────────────┘  └──────────┘  └──────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

**Já temos a topologia hexagonal funcionando.** A evolução é tornar essa estrutura
explícita no código, não reconstruir a plataforma.

---

## Visão Alvo — Hexagonal Formalizado

A imagem alvo mantém o que já existe e adiciona uma camada de **interfaces formais
(ports)** entre o domínio e os adapters.

```
                    ┌────────────────────────────────────┐
                    │      DRIVING ADAPTERS              │
                    │  (quem chama o sistema)            │
                    │                                    │
                    │  REST  •  Chat  •  UI  •  Bot      │
                    │  Teams • DevOps Agent • CLI        │
                    └──────────────┬─────────────────────┘
                                   │
                                   ▼
                    ╔══════════════════════════════════╗
                    ║   APPLICATION (casos de uso)     ║
                    ║                                  ║
                    ║  • InvestigateUseCase            ║
                    ║  • ChatUseCase                   ║
                    ║  • CorrelateSignalsUseCase       ║
                    ║                                  ║
                    ║  ┌────────────────────────────┐  ║
                    ║  │   DOMAIN (núcleo puro)     │  ║
                    ║  │                            │  ║
                    ║  │  • Correlation             │  ║
                    ║  │  • Hypothesis              │  ║
                    ║  │  • Guardrails              │  ║
                    ║  │  • Models                  │  ║
                    ║  │                            │  ║
                    ║  │  Não conhece nada de fora  │  ║
                    ║  └────────────────────────────┘  ║
                    ╚════════════╤═════════════════════╝
                                 │
                  ┌──────────────┴──────────────┐
                  │      DRIVEN PORTS           │
                  │  (interfaces que o domínio  │
                  │   define para o mundo)      │
                  │                             │
                  │  • AlertSource              │
                  │  • IncidentSource           │
                  │  • MetricSource             │
                  │  • TraceSource              │
                  │  • LLMProvider              │
                  │  • CaseFileRepository       │
                  └──────────────┬──────────────┘
                                 │
                                 ▼
              ┌──────────────────────────────────────┐
              │       DRIVEN ADAPTERS                │
              │   (implementações concretas)         │
              │                                      │
              │  GrafanaAdapter  →  AlertSource      │
              │  PGAdapter       →  IncidentSource   │
              │  VMAdapter       →  MetricSource     │
              │  TempoAdapter    →  TraceSource      │
              │  OpenAIAdapter   →  LLMProvider      │
              │  PGStorageAdapt. →  CaseFileRepo     │
              └──────────────────────────────────────┘
```

### Estrutura de pastas alvo (reorganização interna do orchestrator)

```
orchestrator/
├── domain/                          # núcleo puro (já temos a base)
│   ├── models.py
│   ├── correlation.py
│   ├── hypothesis.py
│   └── guardrails.py
│
├── application/                     # casos de uso (formalizar)
│   ├── use_cases/
│   │   ├── investigate.py
│   │   └── chat.py
│   └── ports/                       # interfaces (novo)
│       ├── alert_source.py
│       ├── incident_source.py
│       ├── metric_source.py
│       ├── trace_source.py
│       ├── llm_provider.py
│       └── case_file_repository.py
│
├── infrastructure/                  # adapters (renomear/agrupar)
│   ├── adapters/
│   │   ├── grafana_alert_adapter.py
│   │   ├── pg_incident_adapter.py
│   │   ├── vm_metric_adapter.py
│   │   ├── tempo_trace_adapter.py
│   │   ├── openai_llm_adapter.py
│   │   └── inmemory_repo.py
│   ├── mcp_client.py
│   └── prometheus_metrics.py
│
└── api/                             # driving adapters
    ├── http/
    │   ├── routes.py
    │   └── dependencies.py
    └── main.py
```

**Importante — a infraestrutura permanece distribuída**:

A reorganização hexagonal acontece **dentro do orchestrator** (uma única
codebase). A topologia da plataforma como um todo **continua distribuída
exatamente como hoje**:

| Componente | Deployment hoje | Deployment depois |
|------------|-----------------|-------------------|
| Orchestrator | Pod próprio (`copilot/orchestrator`) | Pod próprio (mesma imagem, código melhor organizado) |
| Grafana MCP | Pod próprio (`observability/grafana-mcp`) | Pod próprio, sem mudanças |
| Incidents PG MCP | Pod próprio (`observability/incidents-pg-mcp`) | Pod próprio, sem mudanças |
| VictoriaMetrics MCP | Pod próprio (`observability/vm-mcp*`) | Pod próprio, sem mudanças |
| Tempo MCP | Pod próprio | Pod próprio, sem mudanças |
| UI Streamlit | Pod próprio (`copilot/ui`) | Pod próprio, sem mudanças |
| LLM Gateway | Serviço externo | Serviço externo, sem mudanças |

**O que muda**: a organização interna do código do orchestrator (pastas,
interfaces, separação domínio/adapters).
**O que NÃO muda**: número de imagens, número de pods, fronteiras de rede,
isolamento de segurança, escalabilidade independente de cada MCP server,
modelo de deploy (Kubernetes), namespaces, network policies.

A separação distribuída por MCP server é uma decisão arquitetural
independente do hexagonal e continua sendo um ponto forte da plataforma:
permite escalar cada fonte de telemetria de forma autônoma, isolar falhas
e atualizar adapters sem redeploy do orchestrator.

### Coexistência das duas versões

Para preservar a versão atual em produção e permitir testes lado a lado,
a reforma vive em uma pasta paralela:

| Pasta | Estado | Quando usar |
|-------|--------|-------------|
| `orchestrator/` | versão atual, intacta | produção e testes do time |
| `orchestrator-hexagonal/` | versão hexagonal | validação da nova organização |

Ambas expõem **as mesmas rotas HTTP**, **as mesmas métricas Prometheus
`observa_*`** e **as mesmas env vars**. Para alternar entre uma e outra
basta apontar a imagem Docker no manifesto K8s.

---

## Plano de Evolução Incremental

A proposta é evoluir em **5 fases pequenas**, cada uma entregando valor isolado.
Não há "big bang" — em qualquer fase pode-se pausar e o sistema continua funcionando.

### Fase 1 — Formalizar Interfaces (1 sprint)
**Objetivo**: documentar contratos que já existem implicitamente.

- Criar `application/ports/` com interfaces (`Protocol` em Python) para cada fonte:
  `AlertSource`, `IncidentSource`, `MetricSource`, `TraceSource`, `LLMProvider`
- Agentes existentes passam a "implementar" essas interfaces
- Zero refatoração de lógica — apenas explicitar o contrato

**Entregável**: equipe nova entende o sistema lendo só os ports.
**Risco**: nenhum. Mudanças aditivas.

### Fase 2 — Extrair Casos de Uso (1 sprint)
**Objetivo**: tornar explícito o "o que" o sistema faz.

- Criar `application/use_cases/investigate.py` e `chat.py`
- Mover lógica de `Orchestrator.investigate()` para `InvestigateUseCase.execute()`
- Endpoints HTTP passam a apenas instanciar e chamar o use case

**Entregável**: testar regra de negócio sem subir HTTP.
**Risco**: baixo. Reorganização sem mudar comportamento.

### Fase 3 — Reorganizar Adapters (1 sprint)
**Objetivo**: separar tecnologia de regra.

- Criar `infrastructure/adapters/` e mover os agentes atuais para lá
- Renomear `agents/grafana.py` → `infrastructure/adapters/grafana_alert_adapter.py`
- Adapters passam a implementar formalmente os ports da Fase 1

**Entregável**: trocar adapter (ex: Grafana → Mimir) sem tocar no domínio.
**Risco**: baixo. Renomeação e reorganização de imports.

### Fase 4 — Inversão de Dependência (1 sprint)
**Objetivo**: domínio passa a receber adapters, não criá-los.

- Adicionar container de injeção de dependência simples (`api/dependencies.py`)
- Use cases recebem ports no construtor
- Adapters concretos são injetados na inicialização

**Entregável**: testar com mocks fica trivial. Substituir tecnologia em runtime
fica possível.
**Risco**: médio. Ajuste em pontos de instanciação. Mitigado por bons testes.

### Fase 5 — Suite de Testes do Domínio (1 sprint)
**Objetivo**: aproveitar a separação para testes rápidos.

- Testes unitários de `correlation`, `hypothesis`, `guardrails` (já isolados)
- Testes de use case com adapters fake (in-memory)
- Testes de integração mantidos para adapters reais

**Entregável**: feedback de regressão em segundos, não minutos.
**Risco**: nenhum. Adição de cobertura.

---

## Cronograma Proposto

```
Sprint 1: Fase 1 — Ports                    [▓▓▓▓▓▓▓▓▓▓]
Sprint 2: Fase 2 — Use Cases                            [▓▓▓▓▓▓▓▓▓▓]
Sprint 3: Fase 3 — Adapters                                       [▓▓▓▓▓▓▓▓▓▓]
Sprint 4: Fase 4 — DI                                                       [▓▓▓▓▓▓▓▓▓▓]
Sprint 5: Fase 5 — Testes                                                              [▓▓▓▓▓▓▓▓▓▓]
```

**Total**: 5 sprints (~10 semanas), executáveis em paralelo com entrega de features.

---

## Critérios de Sucesso

Ao final de cada fase, validar:

| Fase | Critério |
|------|----------|
| 1 | `Protocol`s definidos cobrem 100% das fontes externas |
| 2 | `/investigate` e `/chat` continuam funcionando idênticos ao usuário final |
| 3 | Trocar adapter de uma fonte sem tocar em `domain/` ou `application/` |
| 4 | Use case instanciável com adapters fake em testes |
| 5 | Suite de domínio roda em < 5 segundos |

---

## Benefícios Esperados (mensuráveis)

| Indicador | Antes | Depois |
|-----------|-------|--------|
| Tempo para adicionar nova fonte (Splunk, Athena) | 2-3 dias | 1 dia |
| Tempo de execução dos testes do núcleo | minutos | segundos |
| Onboarding de novo desenvolvedor | 2 semanas | 3-5 dias |
| Resiliência a troca de fornecedor (LLM, banco) | refatoração | troca de adapter |
| Risco de bug ao tocar regra de negócio | médio | baixo |

---

## Considerações Finais

A plataforma atual é **sólida e funcional**. Esta evolução é uma formalização
arquitetural que reforça o caminho que o time já vem trilhando, alinhando o código
a um padrão reconhecido pela indústria (Ports & Adapters).

A proposta:

- **Preserva** todo o trabalho já entregue
- **Mantém** a infraestrutura distribuída (MCP servers, UI e orchestrator continuam em pods independentes)
- **Mantém** o sistema em produção durante toda a evolução
- **Não exige** mudanças de infraestrutura, deployment, namespaces, network policies ou stack
- **Acelera** a velocidade de evolução futura

O custo é tempo de engenharia distribuído em 5 sprints; o retorno é permanente
em produtividade, qualidade e resiliência tecnológica.

---

## Apêndice — Glossário Executivo

- **Domínio**: regra de negócio pura ("como investigar um incidente")
- **Port**: contrato/interface ("preciso buscar alertas")
- **Adapter**: implementação concreta ("buscar alertas no Grafana via HTTP")
- **Driving (entrada)**: quem chama o sistema (REST, UI, bot)
- **Driven (saída)**: o que o sistema chama (banco, LLM, APIs externas)
- **Inversão de dependência**: domínio define o contrato, infraestrutura se adapta
