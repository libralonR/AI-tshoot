# Grafana MCP Server - Guia de Deployment

Guia passo-a-passo para fazer deploy do Grafana MCP Server no Kubernetes.

## Visão Geral

Este deployment cria um servidor MCP (Model Context Protocol) que expõe 4 ferramentas para consultar o Grafana:
- `get_alert_details`: Buscar detalhes de um alerta por UID
- `find_firing_alerts`: Listar alertas ativos (com filtros opcionais)
- `find_dashboards`: Buscar dashboards por labels/tags
- `get_panel_link`: Gerar link direto para painel com time range

## Arquitetura

```
┌─────────────────────────────────────────────────────────────┐
│                     Kubernetes Cluster                       │
│                                                              │
│  ┌────────────────────────────────────────────────────┐    │
│  │           Namespace: observability                  │    │
│  │                                                      │    │
│  │  ┌──────────────────────────────────────────┐     │    │
│  │  │  Deployment: grafana-mcp-server          │     │    │
│  │  │  ┌────────────┐  ┌────────────┐         │     │    │
│  │  │  │   Pod 1    │  │   Pod 2    │  ...    │     │    │
│  │  │  │            │  │            │         │     │    │
│  │  │  │ MCP Server │  │ MCP Server │         │     │    │
│  │  │  └─────┬──────┘  └─────┬──────┘         │     │    │
│  │  └────────┼────────────────┼────────────────┘     │    │
│  │           │                │                       │    │
│  │  ┌────────▼────────────────▼────────────────┐     │    │
│  │  │  Service: grafana-mcp-server (ClusterIP) │     │    │
│  │  └────────┬─────────────────────────────────┘     │    │
│  │           │                                        │    │
│  │  ┌────────▼─────────────────────────────────┐     │    │
│  │  │  ConfigMap: grafana-mcp-config           │     │    │
│  │  │  - GRAFANA_URL                           │     │    │
│  │  │  - GRAFANA_ORG_ID                        │     │    │
│  │  │  - GRAFANA_TIMEOUT_S                     │     │    │
│  │  └──────────────────────────────────────────┘     │    │
│  │                                                     │    │
│  │  ┌──────────────────────────────────────────┐     │    │
│  │  │  Secret: grafana-mcp-secrets             │     │    │
│  │  │  - GRAFANA_TOKEN (base64)                │     │    │
│  │  └──────────────────────────────────────────┘     │    │
│  │                                                     │    │
│  │  ┌──────────────────────────────────────────┐     │    │
│  │  │  HPA: grafana-mcp-server                 │     │    │
│  │  │  Min: 2, Max: 10                         │     │    │
│  │  └──────────────────────────────────────────┘     │    │
│  │                                                     │    │
│  │  ┌──────────────────────────────────────────┐     │    │
│  │  │  NetworkPolicy: grafana-mcp-server       │     │    │
│  │  │  Ingress: copilot namespace              │     │    │
│  │  │  Egress: Grafana + DNS                   │     │    │
│  │  └──────────────────────────────────────────┘     │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Grafana Service (target)                           │    │
│  │  http://grafana.observability.svc.cluster.local:3000│    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## Pré-requisitos

### 1. Ferramentas

- Docker (v20+)
- kubectl (v1.24+)
- make (opcional, mas recomendado)
- Acesso ao cluster Kubernetes
- Acesso a um Container Registry

### 2. Permissões

Você precisa de permissões para:
- Criar namespace
- Criar deployments, services, configmaps, secrets
- Criar network policies
- Criar HPA e PDB

### 3. Grafana

- Grafana rodando e acessível do cluster
- Token de API com permissões de leitura (Viewer role)

## Passo 1: Preparação

### 1.1. Clone o repositório

```bash
cd observa-ai-troubleshooter
```

### 1.2. Configure variáveis de ambiente

```bash
cd k8s/grafana-mcp
cp .env.example .env
vim .env
```

Edite `.env`:
```bash
REGISTRY=your-registry.example.com
IMAGE_NAME=grafana-mcp-server
VERSION=v1.0.0
NAMESPACE=observability
GRAFANA_URL=http://grafana.observability.svc.cluster.local:3000
GRAFANA_TOKEN=glsa_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

### 1.3. Gerar token do Grafana

**Opção A: API Keys (Grafana < v9)**
1. Acesse Grafana UI → Configuration → API Keys
2. Clique em "Add API key"
3. Nome: `mcp-server-readonly`
4. Role: `Viewer`
5. Copie o token gerado

**Opção B: Service Accounts (Grafana v9+, recomendado)**
1. Acesse Grafana UI → Administration → Service Accounts
2. Clique em "Add service account"
3. Nome: `mcp-server`
4. Role: `Viewer`
5. Clique em "Add service account token"
6. Copie o token gerado

## Passo 2: Build da Imagem

### Opção A: Usando Make (recomendado)

```bash
# Build da imagem
make build

# Push para registry
make push
```

### Opção B: Usando script

```bash
# Build e push
./build-and-deploy.sh
```

### Opção C: Manual

```bash
# Copiar código fonte
cp ../../mcp-servers/grafana_v2.py ./grafana_v2.py

# Build
docker build -t your-registry/grafana-mcp-server:v1.0.0 .

# Tag latest
docker tag your-registry/grafana-mcp-server:v1.0.0 \
           your-registry/grafana-mcp-server:latest

# Push
docker push your-registry/grafana-mcp-server:v1.0.0
docker push your-registry/grafana-mcp-server:latest

# Cleanup
rm grafana_v2.py
```

## Passo 3: Configuração do Kubernetes

### 3.1. Criar namespace

```bash
kubectl create namespace observability
```

### 3.2. Atualizar ConfigMap

Edite `configmap.yaml` e ajuste a URL do Grafana:

```yaml
data:
  grafana-url: "http://grafana.observability.svc.cluster.local:3000"
```

Se o Grafana estiver fora do cluster:
```yaml
data:
  grafana-url: "https://grafana.example.com"
```

### 3.3. Criar Secret

**Opção A: Via kubectl (recomendado)**

```bash
kubectl create secret generic grafana-mcp-secrets \
  --from-literal=grafana-token='glsa_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX' \
  --namespace observability
```

**Opção B: Via Make**

```bash
make create-secret
# Será solicitado o token interativamente
```

**Opção C: Editar secret.yaml**

```bash
vim secret.yaml
# Substitua SUBSTITUA_COM_SEU_TOKEN_AQUI pelo token real
kubectl apply -f secret.yaml
```

### 3.4. Atualizar referência da imagem

Edite `deployment.yaml` linha ~35:

```yaml
image: your-registry/grafana-mcp-server:v1.0.0
```

Ou use Kustomize (edite `kustomization.yaml`):

```yaml
images:
- name: your-registry/grafana-mcp-server
  newTag: v1.0.0
```

## Passo 4: Deploy

### Opção A: Usando Make (recomendado)

```bash
# Deploy completo
make all

# Ou passo a passo
make create-namespace
make deploy
make rollout-status
make status
```

### Opção B: Usando Kustomize

```bash
kubectl apply -k .
kubectl rollout status deployment/grafana-mcp-server -n observability
```

### Opção C: Usando kubectl direto

```bash
kubectl apply -f serviceaccount.yaml
kubectl apply -f configmap.yaml
kubectl apply -f secret.yaml
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
kubectl apply -f networkpolicy.yaml
kubectl apply -f hpa.yaml
kubectl apply -f pdb.yaml
```

## Passo 5: Verificação

### 5.1. Verificar pods

```bash
kubectl get pods -n observability -l app=grafana-mcp-server

# Saída esperada:
# NAME                                  READY   STATUS    RESTARTS   AGE
# grafana-mcp-server-7d8f9c5b6d-abc12   1/1     Running   0          1m
# grafana-mcp-server-7d8f9c5b6d-def34   1/1     Running   0          1m
```

### 5.2. Verificar logs

```bash
# Via Make
make logs

# Via kubectl
kubectl logs -n observability -l app=grafana-mcp-server --tail=50 -f
```

Logs esperados:
```
2026-03-05 23:14:45,817 - grafana-mcp - INFO - Starting MCP server...
2026-03-05 23:14:45,820 - grafana-mcp - INFO - Connected to Grafana: http://grafana...
```

### 5.3. Verificar service

```bash
kubectl get svc -n observability grafana-mcp-server

# Saída esperada:
# NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
# grafana-mcp-server   ClusterIP   10.96.123.456   <none>        8080/TCP   1m
```

### 5.4. Verificar HPA

```bash
kubectl get hpa -n observability grafana-mcp-server

# Saída esperada:
# NAME                 REFERENCE                       TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
# grafana-mcp-server   Deployment/grafana-mcp-server   15%/70%, 20%/80%   2         10        2          1m
```

## Passo 6: Teste

### 6.1. Port-forward para teste local

```bash
# Via Make
make port-forward

# Via kubectl
kubectl port-forward -n observability svc/grafana-mcp-server 8080:8080
```

### 6.2. Testar conectividade com Grafana

```bash
# Via Make
make test-grafana-connection

# Via kubectl
POD=$(kubectl get pods -n observability -l app=grafana-mcp-server -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n observability $POD -- \
  python -c "import httpx; print(httpx.get('$GRAFANA_URL/api/health').text)"
```

Saída esperada:
```json
{"database":"ok","version":"10.0.0"}
```

## Passo 7: Integração com Kiro/Copilot

### 7.1. Atualizar configuração do Kiro

Se o Kiro rodar no mesmo cluster, atualize `.kiro/settings/mcp.json`:

```json
{
  "mcpServers": {
    "grafana": {
      "command": "kubectl",
      "args": [
        "exec",
        "-n", "observability",
        "deployment/grafana-mcp-server",
        "--",
        "python", "grafana_v2.py"
      ],
      "disabled": false
    }
  }
}
```

### 7.2. Ou via Service (se Kiro estiver no cluster)

```json
{
  "mcpServers": {
    "grafana": {
      "command": "curl",
      "args": [
        "http://grafana-mcp-server.observability.svc.cluster.local:8080"
      ],
      "disabled": false
    }
  }
}
```

## Troubleshooting

### Problema: Pods não iniciam

```bash
# Ver eventos
kubectl describe pod -n observability -l app=grafana-mcp-server

# Ver logs
kubectl logs -n observability -l app=grafana-mcp-server
```

**Erros comuns:**
- `ImagePullBackOff`: Imagem não encontrada → Verifique registry e tag
- `CrashLoopBackOff`: Erro no código → Verifique logs
- `Missing GRAFANA_URL`: ConfigMap não aplicado → `kubectl apply -f configmap.yaml`
- `Missing GRAFANA_TOKEN`: Secret não criado → Veja Passo 3.3

### Problema: Erro de conexão com Grafana

```bash
# Testar DNS
kubectl run -it --rm debug --image=busybox --restart=Never -- \
  nslookup grafana.observability.svc.cluster.local

# Testar conectividade HTTP
kubectl run -it --rm debug --image=curlimages/curl --restart=Never -- \
  curl -v http://grafana.observability.svc.cluster.local:3000/api/health
```

### Problema: HPA não escala

```bash
# Verificar metrics server
kubectl top nodes
kubectl top pods -n observability

# Ver status do HPA
kubectl describe hpa -n observability grafana-mcp-server
```

Se metrics server não estiver instalado:
```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

## Manutenção

### Atualizar imagem

```bash
# Build nova versão
VERSION=v1.1.0 make build push

# Atualizar deployment
kubectl set image deployment/grafana-mcp-server \
  grafana-mcp=your-registry/grafana-mcp-server:v1.1.0 \
  -n observability

# Acompanhar rollout
kubectl rollout status deployment/grafana-mcp-server -n observability
```

### Rollback

```bash
# Ver histórico
kubectl rollout history deployment/grafana-mcp-server -n observability

# Rollback
kubectl rollout undo deployment/grafana-mcp-server -n observability
```

### Escalar manualmente

```bash
# Via Make
make scale REPLICAS=5

# Via kubectl
kubectl scale deployment/grafana-mcp-server -n observability --replicas=5
```

### Restart

```bash
# Via Make
make restart

# Via kubectl
kubectl rollout restart deployment/grafana-mcp-server -n observability
```

## Limpeza

```bash
# Via Make
make delete

# Via kubectl
kubectl delete -k .

# Deletar namespace (cuidado!)
kubectl delete namespace observability
```

## Próximos Passos

1. Configurar monitoramento (Prometheus + Grafana)
2. Criar dashboards para métricas do MCP server
3. Configurar alertas para falhas
4. Implementar distributed tracing
5. Adicionar rate limiting
6. Criar Helm chart para facilitar deployment
7. Documentar integração com outros MCP servers (VictoriaMetrics, Splunk, etc.)

## Suporte

Para problemas ou dúvidas:
1. Verifique logs: `make logs`
2. Verifique eventos: `make events`
3. Consulte README.md para troubleshooting detalhado
4. Abra issue no repositório
