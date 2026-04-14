# Plano de Ação - Observability Troubleshooting Copilot

**Data**: 2026-04-13  
**Versão**: 1.0  
**Objetivo**: Roadmap executável para próximas melhorias

---

## Sprint 1: Fundação (2 semanas)

### Objetivo: Estabelecer base sólida para produção

### Semana 1: Testes e Métricas

**Dia 1-2: Setup de Testes**
- [ ] Instalar pytest, pytest-asyncio, pytest-cov
- [ ] Criar estrutura de testes: `tests/unit/`, `tests/integration/`, `tests/e2e/`
- [ ] Configurar pytest.ini e coverage

**Dia 3-4: Unit Tests**
- [ ] Testes para `correlation.py` (normalize_labels, extract_correlation_key, correlate_signals)
- [ ] Testes para `guardrails.py` (redact_pii, validate_read_only, validate_evidence_traceability)
- [ ] Testes para `hypothesis.py` (generate_hypotheses, _group_by_component)
- [ ] Meta: 80% coverage em módulos core

**Dia 5: Métricas Prometheus**
- [ ] Criar `orchestrator/metrics.py`
- [ ] Adicionar decorators @track_investigation, @track_mcp_call
- [ ] Implementar endpoint `/metrics`
- [ ] Testar com Prometheus local

### Semana 2: Resiliência e Segurança

**Dia 1-2: Rate Limiting**
- [ ] Criar `orchestrator/rate_limiter.py`
- [ ] Implementar RateLimiter class (in-memory)
- [ ] Adicionar rate limiting no `/chat` endpoint
- [ ] Adicionar headers X-RateLimit-Remaining
- [ ] Testar com múltiplas requisições

**Dia 3-4: Circuit Breaker**
- [ ] Instalar biblioteca circuitbreaker
- [ ] Criar `orchestrator/resilience.py`
- [ ] Adicionar circuit breaker em chamadas MCP
- [ ] Testar com MCP server offline

**Dia 5: Health Checks**
- [ ] Criar `orchestrator/health.py`
- [ ] Implementar check_all_dependencies()
- [ ] Adicionar endpoint `/health/detailed`
- [ ] Documentar formato de resposta

### Entregáveis Sprint 1
- ✅ Testes automatizados com 80% coverage
- ✅ Métricas Prometheus funcionando
- ✅ Rate limiting implementado
- ✅ Circuit breaker implementado
- ✅ Health checks detalhados

---

## Sprint 2: Capacidades (2 semanas)

### Objetivo: Expandir funcionalidades do sistema

### Semana 1: Busca Expandida de Incidentes

**Dia 1-2: Backend**
- [ ] Modificar `_get_related_incidents` em `incidents_pg.py`
- [ ] Suportar filtros: business_capability, business_domain, owner_squad, owner_sre
- [ ] Construir query dinâmica com múltiplas condições
- [ ] Adicionar logs detalhados

**Dia 3: LLM Integration**
- [ ] Atualizar tool definition em `llm_client.py`
- [ ] Adicionar novos parâmetros: business_capability, business_domain, owner_squad
- [ ] Atualizar system prompt em `orchestrator-prompt.md`

**Dia 4-5: Testes**
- [ ] Testes unitários para nova lógica de busca
- [ ] Testes de integração com PostgreSQL
- [ ] Testes E2E via `/chat` endpoint
- [ ] Validar com dados reais

### Semana 2: Cache e Performance

**Dia 1-2: Redis Cache**
- [ ] Setup Redis (docker-compose)
- [ ] Criar `orchestrator/cache.py`
- [ ] Implementar cache para resultados MCP
- [ ] TTL configurável por tipo de dado

**Dia 3-4: Connection Pooling**
- [ ] Implementar connection pool para PostgreSQL
- [ ] Configurar pool size e timeout
- [ ] Adicionar métricas de pool

**Dia 5: Load Tests**
- [ ] Instalar locust
- [ ] Criar `tests/load/locustfile.py`
- [ ] Testar `/investigate` e `/chat` endpoints
- [ ] Documentar resultados

### Entregáveis Sprint 2
- ✅ Busca de incidentes por múltiplas labels
- ✅ Cache Redis implementado
- ✅ Connection pooling PostgreSQL
- ✅ Load tests executados

---

## Sprint 3: Produção (2 semanas)

### Objetivo: Preparar para deploy em produção

### Semana 1: Storage e CI/CD

**Dia 1-3: CaseFile Storage**
- [ ] Criar schema PostgreSQL para CaseFiles
- [ ] Implementar `orchestrator/storage.py`
- [ ] Adicionar métodos: save_case_file, get_case_file, list_case_files
- [ ] Atualizar endpoint `/casefile/{id}`

**Dia 4-5: CI/CD Pipeline**
- [ ] Criar `.github/workflows/ci.yml`
- [ ] Configurar: lint, test, build, push
- [ ] Adicionar quality gates (coverage > 80%)
- [ ] Configurar deploy automático para staging

### Semana 2: Documentação e Observabilidade

**Dia 1-2: OpenAPI/Swagger**
- [ ] Adicionar docstrings completas em endpoints
- [ ] Configurar FastAPI OpenAPI
- [ ] Adicionar exemplos de request/response
- [ ] Publicar docs em `/docs`

**Dia 3: Grafana Dashboards**
- [ ] Criar dashboard para métricas do orchestrator
- [ ] Painéis: latência, throughput, erros, MCP calls
- [ ] Alertas: latência > 5s, error rate > 5%

**Dia 4-5: Runbooks**
- [ ] Documentar procedimentos operacionais
- [ ] Troubleshooting comum
- [ ] Escalation procedures
- [ ] On-call playbook

### Entregáveis Sprint 3
- ✅ CaseFile storage implementado
- ✅ CI/CD pipeline funcionando
- ✅ OpenAPI/Swagger completo
- ✅ Grafana dashboards
- ✅ Runbooks operacionais

---

## Backlog (Futuro)

### Curto Prazo (próximos 3 meses)

**Novas Fontes de Dados**:
- [ ] Integrar Tempo MCP (traces)
- [ ] Integrar Splunk MCP (logs)
- [ ] Integrar VictoriaMetrics MCP (métricas)

**ML e IA**:
- [ ] Implementar ranking de hipóteses com ML
- [ ] Feedback loop (usuários avaliam hipóteses)
- [ ] Aprendizado contínuo

**UX**:
- [ ] Interface web para /chat
- [ ] Visualização de CaseFile
- [ ] Dashboard de investigações

### Médio Prazo (6 meses)

**Auto-Remediation**:
- [ ] Framework de ações seguras
- [ ] Aprovação humana obrigatória
- [ ] Rollback automático

**Multi-tenancy**:
- [ ] Isolamento por tenant
- [ ] Quotas e limites
- [ ] Billing/metering

**Compliance**:
- [ ] Audit log completo
- [ ] GDPR compliance
- [ ] SOC 2 compliance

---

## Métricas de Sucesso

### Sprint 1
- [ ] Coverage de testes > 80%
- [ ] Métricas Prometheus coletadas
- [ ] Rate limit funcionando (429 após limite)
- [ ] Circuit breaker abre após 5 falhas

### Sprint 2
- [ ] Busca por business_capability funciona
- [ ] Cache hit rate > 50%
- [ ] Load test: 100 req/s sem erros

### Sprint 3
- [ ] CaseFiles persistidos em PostgreSQL
- [ ] CI/CD: deploy automático em < 10min
- [ ] Documentação OpenAPI completa
- [ ] Grafana dashboard publicado

---

## Riscos e Mitigações

### Risco 1: Testes Atrasam Desenvolvimento
**Mitigação**: Começar com testes críticos (correlation, guardrails), expandir gradualmente

### Risco 2: Performance Degrada com Cache
**Mitigação**: Monitorar métricas, ajustar TTL, considerar cache warming

### Risco 3: CI/CD Complexo
**Mitigação**: Começar simples (lint + test), adicionar stages gradualmente

### Risco 4: Usuários Não Adotam
**Mitigação**: Piloto controlado, coletar feedback, iterar rapidamente

---

## Checklist de Produção

Antes de deploy em produção, verificar:

**Código**:
- [ ] Testes automatizados com coverage > 80%
- [ ] Sem secrets hardcoded
- [ ] Logging estruturado implementado
- [ ] Error handling em todos os endpoints

**Infraestrutura**:
- [ ] Health checks configurados
- [ ] Métricas Prometheus coletadas
- [ ] Alertas configurados no Grafana
- [ ] Rate limiting implementado
- [ ] Circuit breaker implementado

**Segurança**:
- [ ] PII redaction funcionando
- [ ] Read-only enforcement validado
- [ ] SSL/TLS configurado
- [ ] Network policies aplicadas

**Documentação**:
- [ ] OpenAPI/Swagger completo
- [ ] Runbooks operacionais
- [ ] Architecture diagrams
- [ ] Troubleshooting guide

**Observabilidade**:
- [ ] Logs centralizados
- [ ] Métricas coletadas
- [ ] Traces distribuídos (futuro)
- [ ] Dashboards criados

---

## Próximos Passos Imediatos

**Esta semana**:
1. Criar estrutura de testes (`tests/unit/`, `tests/integration/`)
2. Implementar testes para `correlation.py`
3. Implementar testes para `guardrails.py`
4. Criar `orchestrator/metrics.py` com métricas básicas

**Próxima semana**:
1. Adicionar decorators de métricas
2. Implementar rate limiting
3. Implementar circuit breaker
4. Criar health checks detalhados

**Dúvidas ou bloqueios**: Documentar e escalar imediatamente

---

## Conclusão

Este plano de ação fornece um roadmap claro e executável para as próximas 6 semanas. Priorize:

1. **Sprint 1**: Fundação (testes, métricas, resiliência)
2. **Sprint 2**: Capacidades (busca expandida, cache, performance)
3. **Sprint 3**: Produção (storage, CI/CD, docs)

Mantenha foco em entregar valor incremental a cada sprint. Ajuste o plano baseado em feedback e descobertas.

**Sucesso = Código testado + Métricas visíveis + Sistema resiliente + Documentação completa**
