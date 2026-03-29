# Quick Start

## Docker Compose (Recomendado)

Sobe toda a stack (Orchestrator + 3 MCP servers) com um comando.

### 1. Configurar variáveis

```bash
cp .env.example .env
```

Edite `.env` com suas credenciais:
```env
# Grafana
GRAFANA_URL=https://sua-instancia.grafana.net/
GRAFANA_TOKEN=glsa_SEU_TOKEN_AQUI

# PostgreSQL (AWS RDS)
PG_HOST=sua-instancia.region.rds.amazonaws.com
PG_PORT=5432
PG_DATABASE=incidents
PG_USER=seu-user
PG_PASSWORD=sua-senha

# VictoriaMetrics
VM_INSTANCE_ENTRYPOINT=http://host.docker.internal:8428
```

### 2. Subir a stack

```bash
docker compose up -d
```

Serviços disponíveis:

| Serviço | Porta | Health Check |
|---------|-------|-------------|
| Orchestrator | http://localhost:8080 | `/health` |
| Grafana MCP | http://localhost:8081 | `/health` |
| Incidents PG MCP | http://localhost:8082 | `/health` |
| VictoriaMetrics MCP | http://localhost:8083 | `/health/liveness` |

### 3. Verificar saúde

```bash
curl http://localhost:8080/health
# {"status":"healthy","service":"orchestrator","version":"1.0.0"}

curl http://localhost:8081/health
curl http://localhost:8082/health
```

### 4. Testar investigação

```bash
# Por incidente
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "INCIDENT_ID",
    "value": "INC0012345",
    "user": "sre-oncall"
  }'

# Por alerta Grafana
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "ALERT_UID",
    "value": "abc123def456"
  }'

# Por sintoma livre
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "SYMPTOM",
    "value": "Timeout no checkout em produção"
  }'
```

### 5. Investigação com filtros

O endpoint `/investigate` aceita um campo opcional `filters` para refinar a busca:

```bash
# Filtrar por serviço e squad
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "SYMPTOM",
    "value": "Erro 500 intermitente",
    "filters": {
      "application_service": "api-gateway",
      "owner_squad": "squad-plataforma"
    }
  }'

# Filtrar por severidade e capability
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "SYMPTOM",
    "value": "Lentidão no pagamento",
    "filters": {
      "severidade": "P1",
      "business_capability": "Pagamentos",
      "grafana_folder": "SRE"
    }
  }'

# Filtrar por alertname específico
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "SYMPTOM",
    "value": "CPU alta",
    "filters": {
      "application_service": "order-service",
      "alertname": "HighCPUUsage"
    }
  }'
```

Filtros disponíveis:
- `application_service` — serviço/componente (chave canônica de correlação)
- `owner_squad` — squad responsável
- `severidade` — P1, P2, P3
- `business_capability` — capacidade de negócio
- `grafana_folder` — pasta do dashboard no Grafana
- `alertname` — nome da regra de alerta

### 6. Outros endpoints

```bash
# Steering context carregado
curl http://localhost:8080/steering

# Buscar CaseFile (TODO: storage)
curl http://localhost:8080/casefile/{id}
```

## Desenvolvimento Local (sem Docker)

```bash
cd orchestrator
pip install -r requirements.txt
python orchestrator.py
```

Requer que os MCP servers estejam rodando e as variáveis de ambiente configuradas:
```bash
export GRAFANA_MCP_ENDPOINT="http://localhost:8081"
export INCIDENTS_PG_MCP_ENDPOINT="http://localhost:8082"
export VM_MCP_ENDPOINT="http://localhost:8083"
```

## Logs

```bash
# Todos os serviços
docker compose logs -f

# Apenas orchestrator
docker compose logs -f orchestrator

# Apenas MCP servers
docker compose logs -f grafana-mcp incidents-pg-mcp vm-mcp
```

## Parar

```bash
docker compose down
```
