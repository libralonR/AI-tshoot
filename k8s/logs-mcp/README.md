# Logs Parquet MCP — Deploy K8s

MCP server que lê logs forenses em formato Parquet armazenados em S3
(particionados como `capability=<bcap>/year=YYYY/month=MM/day=DD/hour=HH/`).

Usa **DuckDB** embedded com a extensão `httpfs` — sem cluster Athena/Trino.
Autentica no S3 via **AssumeRole** (STS) ou IRSA, com refresh automático.

Mais detalhes em `mcp-servers/logs_parquet.py` e `mcp-servers/test_logs_parquet.py`.

---

## Arquivos

| Arquivo | Função |
|---------|--------|
| `Dockerfile` | Imagem Python 3.11 com DuckDB + boto3 + extension `httpfs` |
| `requirements.txt` | Dependências da imagem |
| `serviceaccount.yaml` | SA com annotation IRSA |
| `configmap.yaml` | Bucket, região, AssumeRole opcional, limites |
| `secret.yaml` | AWS keys (fallback quando IRSA não disponível) |
| `deployment.yaml` | 2 réplicas, probes em `/health`, `readOnlyRootFilesystem` |
| `service.yaml` | ClusterIP 8080 |
| `networkpolicy.yaml` | Aceita do namespace `copilot`; egress 443 para S3/STS |
| `hpa.yaml` | Auto-scale 2-6 pods (CPU 70%, mem 80%) |
| `pdb.yaml` | minAvailable=1 |
| `kustomization.yaml` | Composição de tudo |

---

## Variáveis de ambiente (ConfigMap)

| Var | Default | Descrição |
|-----|---------|-----------|
| `LOGS_S3_BUCKET` | `observability-data-log` | Bucket S3 com os parquets |
| `LOGS_AWS_REGION` | `us-east-1` | Região AWS |
| `LOGS_ROLE_ARN` | (vazio) | Role para `sts:AssumeRole`. Vazio → usa credential chain (IRSA / instance profile / env keys) |
| `LOGS_ROLE_SESSION_NAME` | `logs-parquet-mcp` | Nome da sessão STS |
| `LOGS_ROLE_DURATION_SECONDS` | `3600` | Duração do AssumeRole |
| `LOGS_DEFAULT_LIMIT` | `500` | Limit default em buscas |
| `LOGS_MAX_LIMIT` | `1000` | Limit máximo (cap) |
| `LOGS_MAX_WINDOW_HOURS` | `24` | Janela máxima permitida |
| `LOGS_MAX_PARTITIONS` | `24` | Máximo de partições horárias por query |
| `LOGS_SCHEMA_CACHE_TTL_SECONDS` | `3600` | TTL do cache de schema |
| `LOGS_DUCKDB_THREADS` | `4` | Threads do DuckDB |
| `MCP_SERVER_MODE` | `sse` | `sse` (REST + SSE) ou `stdio` |
| `MCP_LISTEN_PORT` | `8080` | Porta HTTP |
| `LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR |

---

## Permissões IAM mínimas

O role assumido pelo pod (via IRSA ou via `LOGS_ROLE_ARN` em outra conta)
precisa apenas de:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListLogsBucket",
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:GetBucketLocation"
      ],
      "Resource": "arn:aws:s3:::observability-data-log"
    },
    {
      "Sid": "ReadParquetObjects",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::observability-data-log/*"
    }
  ]
}
```

Se for cross-account (pod numa conta, bucket em outra), o role do pod
também precisa de `sts:AssumeRole` para o role com a policy acima:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "sts:AssumeRole",
    "Resource": "arn:aws:iam::<account-do-bucket>:role/observability-logs-reader"
  }]
}
```

E o role-alvo precisa de uma trust policy permitindo o role do pod.

---

## Configurar IRSA (recomendado)

1. Criar IAM Role com policy acima e trust policy do OIDC do EKS:

```bash
eksctl create iamserviceaccount \
  --cluster=<seu-cluster> \
  --namespace=observability \
  --name=logs-mcp-server \
  --attach-policy-arn=arn:aws:iam::<account>:policy/observability-logs-mcp \
  --override-existing-serviceaccounts \
  --approve
```

2. Verificar a annotation gerada:

```bash
kubectl -n observability get sa logs-mcp-server -o yaml
```

Deve aparecer: `eks.amazonaws.com/role-arn: arn:aws:iam::...:role/...`

3. Se preferir não usar `eksctl`, edite `serviceaccount.yaml` e adicione
   manualmente a annotation antes do `kubectl apply`.

---

## Build & Deploy

### 1. Build da imagem

A partir da **raiz do repositório**:

```bash
docker build \
  -f k8s/logs-mcp/Dockerfile \
  -t your-registry/logs-mcp-server:v1.0.0 \
  .

docker push your-registry/logs-mcp-server:v1.0.0
```

### 2. Atualizar a tag no `kustomization.yaml`

```yaml
images:
- name: your-registry/logs-mcp-server
  newTag: v1.0.0
```

### 3. Ajustar configuração no cluster

Editar `configmap.yaml` para apontar para o seu bucket:

```yaml
data:
  logs-s3-bucket: "observability-data-log"
  logs-aws-region: "us-east-1"
  logs-role-arn: ""   # vazio se IRSA já tem permissão direta
```

### 4. Deploy

```bash
kubectl apply -k k8s/logs-mcp/

# Acompanhar
kubectl -n observability rollout status deploy/logs-mcp-server
kubectl -n observability logs -l app=logs-mcp-server -f
```

### 5. Validar

```bash
# Port-forward
kubectl -n observability port-forward svc/logs-mcp-server 8080:8080

# Health
curl http://localhost:8080/health | jq

# Listar tools disponíveis
curl http://localhost:8080/tools | jq

# Buscar capabilities no bucket (descoberta)
curl -X POST http://localhost:8080/tools/list_capabilities \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{}}' | jq

# Logs de erro do serviço c6pay-receivables-service na última hora
curl -X POST http://localhost:8080/tools/search_logs \
  -H 'Content-Type: application/json' \
  -d '{
    "arguments": {
      "business_capability": "acquirer-c6pay",
      "application_service": "c6pay-receivables-service",
      "level": "ERROR",
      "start": "'$(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ)'",
      "end":   "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
      "limit": 50
    }
  }' | jq
```

---

## Operação

### Escalar manualmente

```bash
kubectl -n observability scale deploy/logs-mcp-server --replicas=4
```

### Diagnóstico rápido

```bash
# Health
kubectl -n observability exec deploy/logs-mcp-server -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/health').read().decode())"

# Verificar credenciais carregadas (sem expor valores)
kubectl -n observability logs deploy/logs-mcp-server | grep AWSCredentialsManager
```

### Sintomas típicos

| Sintoma | Causa provável | Ação |
|---------|----------------|------|
| Health 503 | env vars ausentes (LOGS_S3_BUCKET) | conferir ConfigMap |
| `An error occurred (AccessDenied)` | IAM Policy faltando | revisar permissões S3 |
| `Time window exceeds limit of 24h` | janela > 24h pedida pelo usuário | esperado — orchestrator deve fragmentar |
| `No files found that match` | partição realmente vazia (apesar do filtro) | aumentar log level / verificar layout do bucket |
| Latência alta no primeiro request | cold start DuckDB + extensão httpfs | esperado, ~3-5s. Subsequentes são <1s |

---

## Integração com o Orchestrator

O endpoint do MCP fica disponível como
`http://logs-mcp-server.observability.svc.cluster.local:8080` dentro do cluster.

Para o orchestrator descobrir, basta exportar:

```bash
LOGS_MCP_ENDPOINT="http://logs-mcp-server.observability.svc.cluster.local:8080"
```

A integração propriamente dita (Sprint 4) adiciona este endpoint em
`orchestrator/config.py` e cria `agents/logs.py` análogo ao `agents/traces.py`.

---

## Limpeza

```bash
kubectl delete -k k8s/logs-mcp/
```
