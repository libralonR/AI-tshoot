# Testing Guide - Incidents PG MCP Server

## Overview

Este guia documenta como testar o servidor MCP de incidentes PostgreSQL, incluindo os logs melhorados e os testes robustos implementados.

## Melhorias Implementadas

### 1. Logging Aprimorado

Todos os métodos agora incluem logs estruturados com:

- **Prefixos por função**: `[get_incident]`, `[search_incidents]`, `[get_related_incidents]`, `[get_incident_stats]`
- **Logs de entrada**: Parâmetros recebidos
- **Logs de progresso**: Etapas da execução
- **Logs de saída**: Resultados e métricas
- **Logs de erro**: Tipo de exceção e mensagem

#### Exemplo de Logs

```
2026-04-07 11:00:31 - incidents-pg-mcp - INFO - [get_incident] Starting fetch for incident: INC0012345
2026-04-07 11:00:31 - incidents-pg-mcp - DEBUG - [get_incident] Executing query for INC0012345
2026-04-07 11:00:31 - incidents-pg-mcp - DEBUG - [get_incident] Query completed, result=found
2026-04-07 11:00:31 - incidents-pg-mcp - INFO - [get_incident] Successfully fetched INC0012345 | cmdb_ci_name=aml-worker-service | priority=1 | state=In Progress | assignment_group=Squad Payments | grafana_labels_count=15 | alert_rule_uid=df4m8ngnj6br4e
```

### 2. Testes Robustos

O arquivo `test_incidents_pg.py` foi completamente reescrito com:

- **12 testes via REST** (modo SSE)
- **6 testes diretos** (chamadas de função)
- **Formatação clara** com símbolos ✓/✗
- **Modo verbose** para debug detalhado
- **Sumário de resultados** com taxa de sucesso
- **Exit code** apropriado para CI/CD

## Como Executar os Testes

### Pré-requisitos

```bash
# Variáveis de ambiente necessárias
export PG_HOST=your-postgres-host
export PG_PORT=5432
export PG_DATABASE=incidents
export PG_USER=your-user
export PG_PASSWORD=your-password
export PG_SSLMODE=require
```

### Modo REST (Servidor SSE)

```bash
# 1. Iniciar o servidor em modo SSE
export MCP_SERVER_MODE=sse
export MCP_LISTEN_PORT=8082
python mcp-servers/incidents_pg.py

# 2. Em outro terminal, executar os testes
python mcp-servers/test_incidents_pg.py rest http://localhost:8082

# 3. Com verbose para mais detalhes
python mcp-servers/test_incidents_pg.py rest http://localhost:8082 --verbose
```

### Modo Direto (Sem Servidor)

```bash
# Executar testes diretos (chama funções diretamente)
python mcp-servers/test_incidents_pg.py direct

# Com verbose
python mcp-servers/test_incidents_pg.py direct --verbose
```

## Testes Implementados

### Testes REST (12 testes)

1. **Health Check** - Verifica se o servidor está respondendo
2. **List Tools** - Lista todas as ferramentas disponíveis
3. **Search Incidents (No Filter)** - Busca sem filtros, limite 5
4. **Get Incident by Number** - Busca incidente específico
5. **Search by Application Service** - Filtra por cmdb_ci_name
6. **Search by Priority** - Filtra por prioridade (P1)
7. **Search by Date Range** - Filtra por janela de tempo (últimos 7 dias)
8. **Get Related Incidents by Number** - Busca incidentes relacionados por número
9. **Get Related Incidents by Service** - Busca incidentes relacionados por serviço
10. **Get Incident Stats (by Priority)** - Estatísticas agrupadas por prioridade
11. **Get Incident Stats (by State)** - Estatísticas agrupadas por estado
12. **Get Incident Stats with Filter** - Estatísticas filtradas por serviço

### Testes Diretos (6 testes)

1. **search_incidents** - Busca básica com limite
2. **get_incident** - Busca por número
3. **search_incidents by service** - Busca filtrada por serviço
4. **get_related_incidents** - Busca incidentes relacionados
5. **get_incident_stats (by priority)** - Estatísticas por prioridade
6. **get_incident_stats (by state)** - Estatísticas por estado

## Saída dos Testes

### Exemplo de Saída Normal

```
======================================================================
  Testing Incidents PG MCP via REST
======================================================================
Base URL: http://localhost:8082

▶ Test 1: Health Check
  ✓ PASS - Status: 200

▶ Test 2: List Available Tools
  ✓ PASS - Found 4 tools

▶ Test 3: Search Incidents (No Filter, Limit 5)
  ✓ PASS - Count: 5
    executionTime: 0.123s
    incident_1: INC0012345 | aml-worker-service | P1 | In Progress

...

======================================================================
  Test Summary
======================================================================
Total Tests:  12
Passed:       12 ✓
Failed:       0 ✗
Success Rate: 100.0%

🎉 All tests passed!
```

### Exemplo de Saída Verbose

```
▶ Test 4: Get Incident by Number (INC0012345)
  ✓ PASS
    number: INC0012345
    cmdb_ci_name: aml-worker-service
    priority: 1
    state: In Progress
    assignment_group: Squad Payments
    grafana_labels_count: 15
      label_alertname: Conta, Tempo de resposta
      label_application_service: aml-worker-service
      label_business_capability: aml-pld
      label_owner_squad: squad-payments
      label_Severidade: P1
    alert_rule_uid: df4m8ngnj6br4e
    executionTime: 0.045s
```

## Integração com CI/CD

O script retorna exit code apropriado:
- `0` se todos os testes passaram
- `1` se algum teste falhou

```bash
# Em pipeline CI/CD
python mcp-servers/test_incidents_pg.py rest http://incidents-pg-mcp:8080
if [ $? -ne 0 ]; then
    echo "Tests failed!"
    exit 1
fi
```

## Logs Detalhados por Função

### get_incident

```
[get_incident] Starting fetch for incident: INC0012345
[get_incident] Executing query for INC0012345
[get_incident] Query completed, result=found
[get_incident] Successfully fetched INC0012345 | cmdb_ci_name=aml-worker-service | priority=1 | state=In Progress | assignment_group=Squad Payments | grafana_labels_count=15 | alert_rule_uid=df4m8ngnj6br4e
```

### search_incidents

```
[search_incidents] Starting search with filters: {"application_service": "aml-worker", "priority": "1", "limit": 10}
[search_incidents] Filter: application_service LIKE '%aml-worker%'
[search_incidents] Filter: priority = 1
[search_incidents] Limit: 10 (requested: 10)
[search_incidents] Executing query with 2 conditions
[search_incidents] Query returned 7 rows
[search_incidents] Successfully returned 7 incidents | unique_services=3 | priority_distribution={'1': 7}
```

### get_related_incidents

```
[get_related_incidents] Starting search | number=INC0012345 | application_service=None | time_window=48h
[get_related_incidents] Fetching reference incident: INC0012345
[get_related_incidents] Reference incident found | cmdb_ci_name=aml-worker-service | opened_at=2026-04-06 10:00:00 | sys_id=abc123
[get_related_incidents] Searching for child/sibling incidents
[get_related_incidents] Found 2 incidents by parent relationship
[get_related_incidents] Searching for incidents with same CI: aml-worker-service
[get_related_incidents] Found 5 incidents by CI in time window
[get_related_incidents] Search completed | by_parent=2 | by_ci=5 | total=7
```

### get_incident_stats

```
[get_incident_stats] Starting stats calculation | application_service=aml-worker-service | days=30 | group_by=priority
[get_incident_stats] Filter: application_service LIKE '%aml-worker-service%'
[get_incident_stats] Executing stats query
[get_incident_stats] Query returned 3 groups
[get_incident_stats] Stats calculated | groups=3 | total_incidents=45 | period=30 days
[get_incident_stats] Top 1: 1 = 20 incidents
[get_incident_stats] Top 2: 2 = 15 incidents
[get_incident_stats] Top 3: 3 = 10 incidents
```

## Troubleshooting

### Erro: Connection refused

```bash
# Verificar se o servidor está rodando
curl http://localhost:8082/health

# Verificar logs do servidor
tail -f logs/incidents-pg-mcp.log
```

### Erro: Authentication failed

```bash
# Verificar variáveis de ambiente
echo $PG_USER
echo $PG_HOST

# Testar conexão direta
psql -h $PG_HOST -U $PG_USER -d $PG_DATABASE
```

### Erro: No incidents found

```bash
# Verificar se há dados na tabela
psql -h $PG_HOST -U $PG_USER -d $PG_DATABASE -c "SELECT COUNT(*) FROM incidents_snow;"

# Verificar últimos incidentes
psql -h $PG_HOST -U $PG_USER -d $PG_DATABASE -c "SELECT number, cmdb_ci_name, opened_at FROM incidents_snow ORDER BY opened_at DESC LIMIT 5;"
```

## Métricas de Performance

Os logs incluem `executionTime` para cada operação:

- **get_incident**: ~0.05s (busca por índice)
- **search_incidents**: ~0.1-0.5s (depende dos filtros)
- **get_related_incidents**: ~0.2-0.8s (múltiplas queries)
- **get_incident_stats**: ~0.3-1.0s (agregação)

## Próximos Passos

1. Adicionar testes de carga (locust)
2. Adicionar testes de integração com orchestrator
3. Adicionar métricas Prometheus
4. Adicionar health check detalhado (verificar conexão PG)
5. Adicionar cache para queries frequentes
