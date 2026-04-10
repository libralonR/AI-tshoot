# Guia de Uso do Orchestrator - Exemplos e Explicação de Saídas

## Visão Geral

O Orchestrator é o componente central do **Observability Troubleshooting Copilot** que coordena a investigação de problemas correlacionando dados de múltiplas fontes: alertas (Grafana), incidentes (PostgreSQL/ServiceNow), métricas (VictoriaMetrics), logs (Splunk) e traces (Tempo).

**Endpoint**: `POST /investigate`

**Formato**: JSON

---

## Tipos de Entrada (input_type)

O orchestrator aceita 3 tipos de entrada:

1. **INCIDENT_ID** - Número de incidente do ServiceNow (ex: INC0012345)
2. **ALERT_UID** - UID de alerta do Grafana (ex: df4m8ngnj6br4e)
3. **SYMPTOM** - Descrição livre de sintoma (ex: "alertas", "lentidão no API gateway")

---

## Estrutura da Requisição

```json
{
  "input_type": "INCIDENT_ID | ALERT_UID | SYMPTOM",
  "value": "valor correspondente ao tipo",
  "user": "nome.usuario",
  "filters": {
    "application_service": "nome-do-servico",
    "owner_squad": "nome-do-squad",
    "severidade": "P1 | P2 | P3",
    "business_capability": "nome-da-capability",
    "business_domain": "nome-do-dominio",
    "business_service": "nome-do-servico-negocio",
    "grafana_folder": "nome-da-pasta",
    "env": "production | staging | development",
    "cluster": "nome-do-cluster",
    "namespace": "nome-do-namespace"
  }
}
```

---

## Estrutura da Resposta (CaseFile)

```json
{
  "caseFileId": "uuid-do-caso",
  "scope": {
    "serviceName": "application_service extraído",
    "environment": "ambiente extraído",
    "cluster": "cluster extraído",
    "namespace": "namespace extraído",
    "pod": "pod extraído",
    "deployment": "deployment extraído",
    "additionalLabels": {
      "chave": "valor"
    }
  },
  "timeWindow": {
    "start": "ISO timestamp",
    "end": "ISO timestamp",
    "duration": "1h"
  },
  "evidence": [
    {
      "id": "uuid-da-evidencia",
      "type": "ALERT_FIRING | INCIDENT_RELATED | METRIC_ANOMALY | LOG_ERROR | TRACE_ERROR",
      "source": "grafana-mcp | incidents-pg-mcp | vm-mcp | splunk-mcp | tempo-mcp",
      "query": "query executada",
      "result": { /* dados da evidência */ },
      "timestamp": "ISO timestamp",
      "links": ["url1", "url2"],
      "confidence": 0.0-1.0,
      "redacted": true/false
    }
  ],
  "hypotheses": [
    {
      "id": "uuid-da-hipotese",
      "description": "descrição da hipótese",
      "suspectedComponent": "componente suspeito",
      "rootCause": "causa raiz provável",
      "evidenceIds": ["id1", "id2"],
      "confidence": 0.0-1.0,
      "nextSteps": [
        {
          "action": "ação sugerida",
          "description": "descrição detalhada",
          "query": "query PromQL/SQL/etc",
          "link": "url para dashboard/painel",
          "readOnly": true,
          "priority": "HIGH | MEDIUM | LOW"
        }
      ]
    }
  ],
  "correlationGaps": [
    {
      "missingLabel": "label faltante",
      "affectedSources": ["fonte1", "fonte2"],
      "impact": "impacto da falta",
      "recommendation": "recomendação de correção"
    }
  ],
  "executionTime": 0.37
}
```

---

## Exemplos de Uso

### 1. Busca por Incident ID (ServiceNow)

**Requisição:**
```bash
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "INCIDENT_ID",
    "value": "INC0012345",
    "user": "romulo.ramos"
  }'
```

**O que acontece:**
1. Busca o incidente no PostgreSQL (`incidents_snow`)
2. Extrai `application_service` do campo `description` (PRIORIDADE) ou `cmdb_ci_name` (fallback)
3. Define o scope com `serviceName = application_service`
4. Busca alertas firing no Grafana para esse `application_service`
5. Busca incidentes relacionados:
   - **PRIORIDADE**: Busca no campo `description` por padrões (`application_service=`, `instance=`, `CI:`)
   - **FALLBACK**: Busca no campo `cmdb_ci_name`
   - Busca por `parent_incident` (incidentes filhos/irmãos)
6. Correlaciona evidências usando `application_service` como chave
7. Gera hipóteses baseadas nas evidências

**Saída esperada:**
- Evidence tipo `INCIDENT_RELATED` com dados do incidente
- Evidence tipo `ALERT_FIRING` se houver alertas para o serviço
- Evidence tipo `INCIDENT_RELATED` com incidentes relacionados (agrupados em `by_description`, `by_ci`, `by_parent`)
- Hypotheses com componente suspeito = `application_service` (extraído do `description` ou `cmdb_ci_name`)
- CorrelationGaps se alertas não tiverem `application_service`

**Nota sobre busca de incidentes:**
- O campo `cmdb_ci_name` **nem sempre está preenchido**
- As labels do Grafana estão **SEMPRE** no campo `description`
- A busca **prioriza** o campo `description` para maior cobertura

---

### 2. Busca por Alert UID (Grafana)

**Requisição:**
```bash
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "ALERT_UID",
    "value": "df4m8ngnj6br4e",
    "user": "romulo.ramos"
  }'
```

**O que acontece:**
1. Busca detalhes do alerta no Grafana
2. Extrai labels do alerta (`application_service`, `env`, `cluster`, `namespace`, etc)
3. Define o scope com os labels extraídos
4. Busca outros alertas firing para o mesmo serviço
5. Busca incidentes relacionados usando `application_service`:
   - **PRIORIDADE**: Busca no campo `description` por padrões (`application_service=`, `instance=`, `CI:`)
   - **FALLBACK**: Busca no campo `cmdb_ci_name`
6. Correlaciona evidências
7. Gera hipóteses

**Saída esperada:**
- Evidence tipo `ALERT_FIRING` com detalhes do alerta específico
- Evidence tipo `ALERT_FIRING` com outros alertas do mesmo serviço
- Evidence tipo `INCIDENT_RELATED` se houver incidentes para o serviço
- Scope completo com `serviceName`, `environment`, `cluster`, `namespace`
- Hypotheses com componente suspeito = `application_service`

---

### 3. Busca por Sintoma Livre (SYMPTOM)

#### 3.1. Com filtro `application_service`

**Requisição:**
```bash
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "SYMPTOM",
    "value": "lentidão no serviço",
    "user": "romulo.ramos",
    "filters": {
      "application_service": "aml-worker-service",
      "env": "production"
    }
  }'
```

**O que acontece:**
1. Define scope com `serviceName = aml-worker-service` e `environment = production`
2. Busca alertas firing para esse serviço
3. Busca incidentes relacionados usando `application_service = aml-worker-service`:
   - **PRIORIDADE**: Busca no campo `description` por padrões (`application_service=aml-worker-service`, `instance=aml-worker-service`, `CI:aml-worker-service`)
   - **FALLBACK**: Busca no campo `cmdb_ci_name = aml-worker-service`
4. Correlaciona evidências
5. Gera hipóteses

**Saída esperada:**
- Evidence tipo `ALERT_FIRING` se houver alertas
- Evidence tipo `INCIDENT_RELATED` se houver incidentes (busca prioritária no `description`)
- Scope com `serviceName = aml-worker-service`
- Hypotheses com componente suspeito = `aml-worker-service`

**Nota**: A busca de incidentes encontrará resultados mesmo se `cmdb_ci_name` estiver vazio, pois busca no campo `description` onde as labels do Grafana estão sempre presentes.

---

#### 3.2. Com filtro `business_capability` (SEU EXEMPLO)

**Requisição:**
```bash
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "SYMPTOM",
    "value": "alertas",
    "user": "romulo.ramos",
    "filters": {
      "business_capability": "aml-pld"
    }
  }'
```

**O que acontece:**
1. Define scope com `serviceName = None` (não há `application_service` nos filtros)
2. Adiciona `business_capability: aml-pld` em `additionalLabels`
3. Busca alertas firing com label `business_capability = aml-pld`
4. **BUG**: Não busca incidentes porque `serviceName = None` e `incident_number = None`
5. Correlaciona apenas alertas
6. Gera hipóteses baseadas apenas em alertas

**Saída real (seu exemplo):**
```json
{
  "caseFileId": "2895db2f-b27e-457b-bca6-b8f25834229e",
  "scope": {
    "serviceName": null,
    "environment": null,
    "cluster": null,
    "namespace": null,
    "pod": null,
    "deployment": null,
    "additionalLabels": {
      "business_capability": "aml-pld"
    }
  },
  "timeWindow": {
    "start": "2026-04-07T10:00:31.063988",
    "end": "2026-04-07T11:00:31.063988",
    "duration": "1h"
  },
  "evidence": [
    {
      "id": "5d32d8d9-da5d-4de0-bd88-84dbd08da775",
      "type": "ALERT_FIRING",
      "source": "grafana-mcp",
      "query": "find_firing_alerts(labels={'business_capability': 'aml-pld'})",
      "result": {
        "fingerprint": "e92c58e454c3d194",
        "status": {
          "state": "active"
        },
        "labels": {
          "alertname": "Conta, Tempo de resposta - TESTE ROMULO",
          "application_service": "aml-worker-service",
          "business_capability": "aml-pld"
        },
        "startsAt": "2026-04-07T09:10:10.000Z",
        "endsAt": "2026-04-07T11:04:10.000Z",
        "generatorURL": "https://metrics.hom.corp/alerting/grafana/df4m8ngnj6br4e/view"
      },
      "confidence": 0.68
    }
  ],
  "hypotheses": [
    {
      "id": "810fbeab-9b3a-4f80-84c4-7ddf4487a947",
      "description": "Issue detected in aml-worker-service",
      "suspectedComponent": "aml-worker-service",
      "rootCause": "Alert threshold breached, requires investigation",
      "confidence": 0.7,
      "nextSteps": [
        {
          "action": "Check resource metrics",
          "description": "Review CPU, memory, and network metrics for aml-worker-service",
          "query": "rate(container_cpu_usage_seconds_total{pod=~\"aml-worker-service.*\"}[5m])",
          "priority": "HIGH"
        }
      ]
    }
  ],
  "correlationGaps": [],
  "executionTime": 0.37
}
```

**Análise da saída:**
- ✅ Encontrou 1 alerta firing para `business_capability: aml-pld`
- ✅ Alerta contém `application_service: aml-worker-service`
- ❌ **NÃO buscou incidentes** relacionados ao `aml-worker-service`
- ✅ Gerou hipótese baseada no alerta
- ⚠️ Confidence reduzida (0.68) porque há apenas 1 evidência
- ⚠️ Sem `correlationGaps` porque o alerta tem todos os labels padrão

**Por que não buscou incidentes?**

No código `orchestrator.py`, a busca de incidentes só é executada quando há `serviceName` ou `incident_number`:

```python
async def _gather_signals(self, case_file: CaseFile) -> List[Evidence]:
    tasks = [grafana_agent.find_firing_alerts(case_file.scope)]
    
    ci_name = case_file.scope.serviceName  # None neste caso
    additional = case_file.scope.additionalLabels or {}
    inc_number = additional.get("incident_number")  # None neste caso
    
    if inc_number or ci_name:  # Falso porque ambos são None
        tasks.append(
            incidents_agent.find_related_incidents(
                number=inc_number, cmdb_ci_name=ci_name
            )
        )
```

**Limitação conhecida**: Quando o filtro é apenas por `business_capability`, `owner_squad`, ou `severidade` (sem `application_service`), o orchestrator não busca incidentes porque `serviceName = None`.

**Workaround**: Sempre incluir `application_service` nos filtros quando quiser correlacionar com incidentes.

**Nota sobre a busca de incidentes**: Quando a busca é executada (com `application_service` presente), ela **prioriza** o campo `description` onde as labels do Grafana estão sempre presentes, garantindo maior cobertura mesmo quando `cmdb_ci_name` está vazio.

---

#### 3.3. Com filtro `owner_squad`

**Requisição:**
```bash
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "SYMPTOM",
    "value": "problemas no squad",
    "user": "romulo.ramos",
    "filters": {
      "owner_squad": "squad-payments"
    }
  }'
```

**O que acontece:**
1. Define scope com `serviceName = None`
2. Adiciona `owner_squad: squad-payments` em `additionalLabels`
3. Busca alertas firing com label `owner_squad = squad-payments`
4. **BUG**: Não busca incidentes (mesmo problema)
5. Correlaciona apenas alertas

**Saída esperada:**
- Evidence tipo `ALERT_FIRING` para todos os alertas do squad
- Sem evidências de incidentes
- Hypotheses baseadas apenas em alertas

---

#### 3.4. Com filtro `severidade`

**Requisição:**
```bash
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "SYMPTOM",
    "value": "alertas críticos",
    "user": "romulo.ramos",
    "filters": {
      "severidade": "P1"
    }
  }'
```

**O que acontece:**
1. Define scope com `serviceName = None`
2. Adiciona `severidade: P1` em `additionalLabels`
3. Busca alertas firing com label `Severidade = P1`
4. **BUG**: Não busca incidentes
5. Correlaciona apenas alertas

---

#### 3.5. Sintoma com keyword extraction (fallback)

**Requisição:**
```bash
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "SYMPTOM",
    "value": "api-gateway está lento em production",
    "user": "romulo.ramos"
  }'
```

**O que acontece:**
1. Analisa o texto do sintoma
2. Detecta "api-gateway" → define `serviceName = api-gateway`
3. Detecta "production" → define `environment = production`
4. Busca alertas firing para `api-gateway`
5. Busca incidentes relacionados usando `cmdb_ci_name = api-gateway`
6. Correlaciona evidências

**Saída esperada:**
- Evidence tipo `ALERT_FIRING` se houver alertas
- Evidence tipo `INCIDENT_RELATED` se houver incidentes
- Scope com `serviceName = api-gateway` e `environment = production`

**Limitação**: Apenas reconhece keywords hardcoded:
- Services: `api-gateway`, `auth-service`
- Environments: `production`, `prod`, `staging`

---

### 4. Combinação de Filtros

**Requisição:**
```bash
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "SYMPTOM",
    "value": "problemas no serviço",
    "user": "romulo.ramos",
    "filters": {
      "application_service": "payment-api",
      "env": "production",
      "business_capability": "payments",
      "owner_squad": "squad-payments",
      "severidade": "P1"
    }
  }'
```

**O que acontece:**
1. Define scope com `serviceName = payment-api` e `environment = production`
2. Adiciona outros filtros em `additionalLabels`
3. Busca alertas firing com TODOS os filtros
4. Busca incidentes relacionados usando `application_service = payment-api`:
   - **PRIORIDADE**: Busca no campo `description` por padrões
   - **FALLBACK**: Busca no campo `cmdb_ci_name`
5. Correlaciona evidências

**Saída esperada:**
- Evidence tipo `ALERT_FIRING` filtrados por todos os labels
- Evidence tipo `INCIDENT_RELATED` para o serviço
- Scope completo
- Hypotheses com alta confidence se houver múltiplas evidências correlacionadas

---

## Correlação de Evidências

### Chave de Correlação

O orchestrator usa os seguintes labels para correlacionar evidências:

```python
standard_labels = [
    "application_service",
    "owner_squad",
    "severity",
    "env",
    "cluster",
    "namespace",
    "pod",
    "deployment",
    "trace_id"
]
```

### Normalização de Labels

Aliases são normalizados para labels canônicos:

| Original                  | Fonte         | Canônico              | Observação |
|---------------------------|---------------|-----------------------|------------|
| `application_service`     | Grafana       | `application_service` | |
| `cmdb_ci_name`            | Incidentes PG | `application_service` | **Nem sempre preenchido** |
| `description` (parseado)  | Incidentes PG | `application_service` | **SEMPRE preenchido - busca prioritária** |
| `service.name`            | Traces        | `application_service` | |
| `owner_squad`             | Grafana       | `owner_squad`         | |
| `assignment_group_name`   | Incidentes PG | `owner_squad`         | |
| `Severidade`              | Grafana       | `severity`            | |
| `priority`                | Incidentes PG | `severity`            | |

**IMPORTANTE**: A busca de incidentes **prioriza** o campo `description` (sempre preenchido com labels do Grafana) sobre `cmdb_ci_name` (nem sempre preenchido).

### Ajuste de Confidence

```python
confidence_adjustments = {
    "multiple_signals_correlated": 1.2,  # 2+ evidências correlacionadas
    "single_signal": 0.8,                # Apenas 1 evidência
    "has_trace_id": 1.1,                 # Evidência com trace_id
    "has_firing_alert": 1.1              # Evidência com alerta firing
}
```

### Correlation Gaps

Quando uma evidência não pode ser correlacionada (falta labels padrão), o orchestrator:
1. Reduz confidence em 50% (`confidence *= 0.5`)
2. Adiciona um `CorrelationGap` com:
   - `missingLabel`: labels faltantes
   - `affectedSources`: fonte da evidência
   - `impact`: "Cannot correlate with other signals"
   - `recommendation`: sugestão de adicionar os labels

---

## Geração de Hipóteses

O `HypothesisGenerator` analisa as evidências e gera hipóteses baseadas em:

1. **Componente suspeito**: Extraído de `application_service` ou `cmdb_ci_name`
2. **Causa raiz**: Baseada no tipo de evidência:
   - `ALERT_FIRING`: "Alert threshold breached, requires investigation"
   - `INCIDENT_RELATED`: "Related incident detected"
   - `METRIC_ANOMALY`: "Metric anomaly detected"
   - `LOG_ERROR`: "Error pattern detected in logs"
   - `TRACE_ERROR`: "Trace error detected"

3. **Próximos passos**: Ações sugeridas (read-only):
   - Verificar métricas de recursos (CPU, memória, rede)
   - Verificar mudanças recentes (deployments, configs)
   - Verificar logs de erro
   - Verificar traces lentos
   - Verificar dashboards relacionados

4. **Confidence**: Calculada baseada em:
   - Número de evidências correlacionadas
   - Presença de trace_id
   - Presença de alertas firing
   - Completude dos labels

---

## Problemas Conhecidos

### 1. Busca de Incidentes com Filtros Não-Service

**Problema**: Quando `input_type = SYMPTOM` com filtros que não incluem `application_service`, o orchestrator não busca incidentes relacionados.

**Cenários afetados**:
- Filtro por `business_capability`
- Filtro por `owner_squad`
- Filtro por `severidade`
- Filtro por `grafana_folder`

**Causa**: Código em `_gather_signals` verifica apenas `serviceName` e `incident_number`, que são `None` nesses casos.

**Workaround**: Sempre incluir `application_service` nos filtros quando quiser correlacionar com incidentes.

**Solução proposta**: Extrair `application_service` dos alertas encontrados e usar para buscar incidentes em uma segunda fase.

**Nota**: Quando a busca de incidentes é executada (com `application_service` presente), ela utiliza a **estratégia de busca prioritária no campo `description`**, garantindo maior cobertura mesmo quando `cmdb_ci_name` está vazio.

---

### 2. Keyword Extraction Limitada

**Problema**: O fallback de extração de keywords do sintoma só reconhece services e environments hardcoded.

**Limitação**: Não funciona para novos serviços ou ambientes não mapeados.

**Workaround**: Usar filtros explícitos em vez de depender da extração automática.

---

### 3. Time Window Fixo

**Problema**: O time window é sempre fixo em 1 hora (última hora).

**Limitação**: Não permite investigar problemas históricos ou ajustar a janela de tempo.

**Workaround**: Nenhum no momento (requer mudança no código).

---

## Melhores Práticas

### 1. Use `application_service` sempre que possível
```json
{
  "input_type": "SYMPTOM",
  "value": "descrição do problema",
  "filters": {
    "application_service": "nome-do-servico"
  }
}
```

### 2. Combine filtros para resultados mais precisos
```json
{
  "input_type": "SYMPTOM",
  "value": "descrição do problema",
  "filters": {
    "application_service": "nome-do-servico",
    "env": "production",
    "severidade": "P1"
  }
}
```

### 3. Use ALERT_UID quando souber o alerta específico
```json
{
  "input_type": "ALERT_UID",
  "value": "df4m8ngnj6br4e"
}
```

### 4. Use INCIDENT_ID para investigar incidentes conhecidos
```json
{
  "input_type": "INCIDENT_ID",
  "value": "INC0012345"
}
```

---

## Endpoint /chat (LLM-powered)

Alternativa conversacional ao `/investigate`:

**Requisição:**
```bash
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Quais alertas estão firing para aml-pld?",
    "session_id": "optional-session-id"
  }'
```

**Vantagens**:
- Interface conversacional
- LLM decide quais tools chamar
- Suporta follow-up questions
- Resposta em linguagem natural

**Desvantagens**:
- Requer `OPENAI_API_KEY` configurada
- Mais lento que `/investigate`
- Não retorna CaseFile estruturado

---

## Resumo de Tipos de Evidência

| Tipo                | Fonte              | Descrição                                    |
|---------------------|--------------------|----------------------------------------------|
| `ALERT_FIRING`      | grafana-mcp        | Alerta ativo no Grafana                      |
| `INCIDENT_RELATED`  | incidents-pg-mcp   | Incidente do ServiceNow/PostgreSQL           |
| `METRIC_ANOMALY`    | vm-mcp             | Anomalia detectada em métrica                |
| `LOG_ERROR`         | splunk-mcp         | Padrão de erro detectado em logs             |
| `TRACE_ERROR`       | tempo-mcp          | Erro detectado em trace                      |
| `TRACE_SLOW_SPAN`   | tempo-mcp          | Span lento detectado em trace                |
| `DASHBOARD_PANEL`   | grafana-mcp        | Painel de dashboard relevante                |
| `CHANGE_RECENT`     | servicenow/git     | Mudança recente (deploy, config)             |

---

## Conclusão

O Orchestrator é uma ferramenta poderosa para triagem e análise de problemas, mas tem limitações conhecidas na correlação de evidências quando filtros não incluem `application_service`. Para melhores resultados, sempre forneça o máximo de contexto possível nos filtros, especialmente `application_service` e `env`.

### Melhorias Implementadas na Busca de Incidentes

A busca de incidentes foi otimizada para lidar com o fato de que o campo `cmdb_ci_name` **nem sempre está preenchido**:

- ✅ **Busca prioritária no campo `description`**: Onde as labels do Grafana estão **SEMPRE** presentes
- ✅ **Múltiplos padrões de busca**: `application_service=`, `instance=`, `CI:`, `Fingerprint:`
- ✅ **Fallback automático**: Usa `cmdb_ci_name` quando necessário
- ✅ **Deduplicação automática**: Remove duplicatas entre as buscas
- ✅ **Parsing automático**: Extrai labels do Grafana do campo `description`
- ✅ **Resultado estruturado**: Agrupa incidentes por origem (`by_description`, `by_ci`, `by_parent`)

Para mais detalhes, consulte: `docs/INCIDENTS_SEARCH_STRATEGY.md`
