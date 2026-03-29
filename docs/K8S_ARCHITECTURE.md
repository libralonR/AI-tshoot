# Arquitetura Kubernetes - Observability Troubleshooting Copilot

## Visão Geral

A arquitetura é composta por **2 camadas distintas**:

1. **MCP Servers Layer**: Conectores stateless para fontes de dados
2. **Orchestrator Layer**: Cérebro inteligente com prompts, guardrails e lógica de negócio

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Kubernetes Cluster                           │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │                  Namespace: copilot                         │    │
│  │                                                              │    │
│  │  ┌────────────────────────────────────────────────────┐    │    │
│  │  │  Orchestrator Deployment                           │    │    │
│  │  │  ┌──────────────────────────────────────────┐     │    │    │
│  │  │  │  Container: orchestrator                 │     │    │    │
│  │  │  │  - orchestrator.py                       │     │    │    │
│  │  │  │  - /app/steering/*.md                    │     │    │    │
│  │  │  │  - /app/prompts/*.md                     │     │    │    │
│  │  │  │  - /app/specs/*.md                       │     │    │    │
│  │  │  │  - /app/guardrails/*.py                  │     │    │    │
│  │  │  └──────────────────────────────────────────┘     │    │    │
│  │  │                                                     │    │    │
│  │  │  ConfigMap: orchestrator-config                    │    │    │
│  │  │  - MCP server endpoints                            │    │    │
│  │  │  - Correlation rules                               │    │    │
│  │  │                                                     │    │    │
│  │  │  Secret: orchestrator-secrets                      │    │    │
│  │  │  - LLM API keys (OpenAI/Anthropic)                 │    │    │
│  │  └────────────────────────────────────────────────────┘    │    │
│  │                                                              │    │
│  │  ┌────────────────────────────────────────────────────┐    │    │
│  │  │  Service: orchestrator-api                         │    │    │
│  │  │  Type: ClusterIP                                   │    │    │
│  │  │  Port: 8080                                        │    │    │
│  │  └────────────────────────────────────────────────────┘    │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │                  Namespace: observability                   │    │
│  │                                                              │    │
│  │  ┌──────────────────┐  ┌──────────────────┐               │    │
│  │  │ Grafana MCP      │  │ VictoriaMetrics  │               │    │
│  │  │ Deployment       │  │ MCP Deployment   │               │    │
│  │  └──────────────────┘  └──────────────────┘               │    │
│  │                                                              │    │
│  │  ┌──────────────────┐  ┌──────────────────┐               │    │
│  │  │ Splunk MCP       │  │ Tempo MCP        │               │    │
│  │  │ Deployment       │  │ Deployment       │               │    │
│  │  └──────────────────┘  └──────────────────┘               │    │
│  │                                                              │    │
│  │  ┌──────────────────┐  ┌──────────────────┐               │    │
│  │  │ ServiceNow MCP   │  │ Athena MCP       │               │    │
│  │  │ Deployment       │  │ Deployment       │               │    │
│  │  └──────────────────┘  └──────────────────┘               │    │
│  └──────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

## Componentes

### 1. MCP Servers (Namespace: observability)

**Propósito**: Conectores stateless que expõem APIs das fontes de dados via protocolo MCP

**Características**:
- Stateless (sem estado)
- Sem lógica de negócio
- Apenas tradução de protocolo (HTTP → MCP)
- Não contêm prompts, steering ou specs
- Escaláveis horizontalmente

**Deployments**:
```
observability/
├── grafana-mcp-server (2 replicas)
├── incidents-pg-mcp-server (2 replicas)  ← PostgreSQL/AWS RDS
├── victoriametrics-mcp-server (futuro)
├── splunk-mcp-server (futuro)
├── tempo-mcp-server (futuro)
└── athena-mcp-server (futuro)
```

**Exemplo de Dockerfile (MCP Server)**:
```dockerfile
FROM python:3.11-slim
WORKDIR /app

# Apenas código do servidor MCP
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY grafana_v2.py .

# Sem steering, prompts ou specs!
USER 1000
CMD ["python", "grafana_v2.py"]
```

### 2. Orchestrator (Namespace: copilot)

**Propósito**: Cérebro inteligente que coordena investigações, aplica guardrails e gera hipóteses

**Características**:
- Stateful (mantém CaseFiles)
- Contém toda a lógica de negócio
- Usa LLM (OpenAI/Anthropic) para raciocínio
- Aplica guardrails (PII redaction, read-only, evidence-based)
- Correlaciona sinais de múltiplas fontes

**Estrutura de Diretórios**:
```
/app/
├── orchestrator.py          # FastAPI app + endpoints + Orchestrator class
├── models.py                # Dataclasses e Enums (Input, Scope, Evidence, CaseFile...)
├── config.py                # Config, MCP endpoints, label aliases, steering loader
├── mcp_client.py            # MCPClient (comunicação HTTP com MCP servers)
├── guardrails.py            # PII redaction, read-only enforcement, evidence validation
├── correlation.py           # CorrelationEngine (normalização, correlação, gaps)
├── hypothesis.py            # HypothesisGenerator (gera e rankeia hipóteses)
├── agents/
│   ├── __init__.py
│   ├── grafana.py           # GrafanaAgent (alertas, dashboards)
│   └── incidents.py         # IncidentsAgent (incidentes PostgreSQL)
├── steering/                # Contexto persistente
│   ├── product.md
│   ├── tech.md
│   ├── correlation-keys.md
│   └── structure.md
├── prompts/                 # System prompts (para integração LLM)
│   ├── orchestrator-prompt.md
│   ├── grafana-agent-prompt.md
│   └── incidents-agent-prompt.md
└── specs/
    └── design-summary.md
```

**Dockerfile (Orchestrator)**:
```dockerfile
FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code (modular)
COPY orchestrator.py config.py models.py mcp_client.py guardrails.py correlation.py hypothesis.py ./
COPY agents/ ./agents/

# Context baked into image
COPY steering/ ./steering/
COPY prompts/ ./prompts/
COPY specs/ ./specs/

USER 1000
CMD ["python", "orchestrator.py"]
```

## Fluxo de Comunicação

```
User Request
     │
     ▼
┌─────────────────────┐
│  Orchestrator API   │  (copilot namespace)
│  (Port 8080)        │
└─────────────────────┘
     │
     │ 1. Parse input
     │ 2. Create CaseFile
     │ 3. Load steering context
     │ 4. Apply system prompts
     │
     ├──────────────────────────────────────────────┐
     │                                               │
     ▼                                               ▼
┌──────────────────┐                    ┌──────────────────┐
│ Grafana Agent    │                    │ Metrics Agent    │
└──────────────────┘                    └──────────────────┘
     │                                               │
     │ MCP Protocol                                  │ MCP Protocol
     ▼                                               ▼
┌──────────────────┐                    ┌──────────────────┐
│ Grafana MCP      │ (observability ns) │ VictoriaMetrics  │
│ Service          │                    │ MCP Service      │
│ :8080            │                    │ :8080            │
└──────────────────┘                    └──────────────────┘
     │                                               │
     │ HTTP/gRPC                                     │ HTTP
     ▼                                               ▼
┌──────────────────┐                    ┌──────────────────┐
│ Grafana API      │                    │ VictoriaMetrics  │
│ :3000            │                    │ API :8428        │
└──────────────────┘                    └──────────────────┘
```

## ConfigMaps e Secrets

### Orchestrator ConfigMap
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: orchestrator-config
  namespace: copilot
data:
  mcp-servers.yaml: |
    servers:
      grafana:
        endpoint: http://grafana-mcp-server.observability.svc.cluster.local:8080
        timeout: 15s
      victoriametrics:
        endpoint: http://victoriametrics-mcp-server.observability.svc.cluster.local:8080
        timeout: 15s
      splunk:
        endpoint: http://splunk-mcp-server.observability.svc.cluster.local:8080
        timeout: 30s
      tempo:
        endpoint: http://tempo-mcp-server.observability.svc.cluster.local:8080
        timeout: 15s
      incidents-pg:
        endpoint: http://incidents-pg-mcp-server.observability.svc.cluster.local:8080
        timeout: 15s
      athena:
        endpoint: http://athena-mcp-server.observability.svc.cluster.local:8080
        timeout: 60s
  
  correlation-rules.yaml: |
    standard_labels:
      - service.name
      - env
      - cluster
      - namespace
      - pod
      - deployment
      - trace_id
    
    confidence_adjustments:
      multiple_signals_correlated: 1.2
      single_signal: 0.8
      has_trace_id: 1.1
      has_firing_alert: 1.1
```

### Orchestrator Secret
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: orchestrator-secrets
  namespace: copilot
type: Opaque
stringData:
  # LLM API keys
  openai-api-key: "sk-..."
  anthropic-api-key: "sk-ant-..."
  
  # Optional: Database credentials for CaseFile storage
  postgres-url: "postgresql://user:pass@postgres:5432/copilot"
```

## Volumes e Persistência

### Opção 1: Bake into Image (Recomendado para PoC)
- Steering, prompts e specs são copiados para a imagem Docker
- Vantagem: Simples, versionado com código
- Desvantagem: Precisa rebuild para atualizar

### Opção 2: ConfigMaps (Recomendado para Produção)
- Steering e prompts em ConfigMaps
- Specs em ConfigMaps ou Git repo
- Vantagem: Atualização sem rebuild
- Desvantagem: Mais complexo

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: orchestrator-steering
  namespace: copilot
data:
  product.md: |
    # Observability Troubleshooting Copilot (PoC)
    ...
  
  tech.md: |
    # Stack & Guardrails
    ...
  
  structure.md: |
    # Estrutura do repositório
    ...
  
  correlation-keys.md: |
    # Regras de correlação
    ...
```

Montar como volume:
```yaml
volumes:
- name: steering
  configMap:
    name: orchestrator-steering
- name: prompts
  configMap:
    name: orchestrator-prompts
- name: specs
  configMap:
    name: orchestrator-specs

volumeMounts:
- name: steering
  mountPath: /app/steering
  readOnly: true
- name: prompts
  mountPath: /app/prompts
  readOnly: true
- name: specs
  mountPath: /app/specs
  readOnly: true
```

### Opção 3: Git Sync Sidecar (Avançado)
- Sidecar container que sincroniza steering/prompts de um Git repo
- Vantagem: GitOps, versionamento, rollback fácil
- Desvantagem: Mais complexo

```yaml
containers:
- name: orchestrator
  image: orchestrator:latest
  volumeMounts:
  - name: steering
    mountPath: /app/steering

- name: git-sync
  image: k8s.gcr.io/git-sync:v3.6.3
  args:
  - --repo=https://github.com/your-org/copilot-config
  - --branch=main
  - --root=/git
  - --dest=steering
  volumeMounts:
  - name: steering
    mountPath: /git
```

## CaseFile Storage

### Opção 1: PostgreSQL (Recomendado)
```yaml
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: copilot
spec:
  selector:
    app: postgres
  ports:
  - port: 5432
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  namespace: copilot
spec:
  serviceName: postgres
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
      - name: postgres
        image: postgres:15
        env:
        - name: POSTGRES_DB
          value: copilot
        - name: POSTGRES_USER
          valueFrom:
            secretKeyRef:
              name: postgres-secret
              key: username
        - name: POSTGRES_PASSWORD
          valueFrom:
            secretKeyRef:
              name: postgres-secret
              key: password
        volumeMounts:
        - name: data
          mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:
  - metadata:
      name: data
    spec:
      accessModes: ["ReadWriteOnce"]
      resources:
        requests:
          storage: 10Gi
```

### Opção 2: S3/Object Storage
- CaseFiles salvos como JSON em S3
- Vantagem: Escalável, barato
- Desvantagem: Queries mais lentas

## Network Policies

### Orchestrator Network Policy
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: orchestrator
  namespace: copilot
spec:
  podSelector:
    matchLabels:
      app: orchestrator
  policyTypes:
  - Ingress
  - Egress
  
  ingress:
  # Allow from ingress controller
  - from:
    - namespaceSelector:
        matchLabels:
          name: ingress-nginx
    ports:
    - protocol: TCP
      port: 8080
  
  egress:
  # Allow to MCP servers
  - to:
    - namespaceSelector:
        matchLabels:
          name: observability
    - podSelector:
        matchLabels:
          component: mcp-server
    ports:
    - protocol: TCP
      port: 8080
  
  # Allow to LLM APIs (OpenAI, Anthropic)
  - to:
    - namespaceSelector: {}
    ports:
    - protocol: TCP
      port: 443
  
  # Allow to PostgreSQL
  - to:
    - podSelector:
        matchLabels:
          app: postgres
    ports:
    - protocol: TCP
      port: 5432
  
  # DNS
  - to:
    - namespaceSelector:
        matchLabels:
          name: kube-system
    ports:
    - protocol: UDP
      port: 53
```

## Deployment Strategy

### 1. Deploy MCP Servers (observability namespace)
```bash
kubectl apply -k k8s/grafana-mcp/
kubectl apply -k k8s/incidents-pg-mcp/
# Futuros:
# kubectl apply -k k8s/victoriametrics-mcp/
# kubectl apply -k k8s/splunk-mcp/
# kubectl apply -k k8s/tempo-mcp/
# kubectl apply -k k8s/athena-mcp/
```

### 2. Deploy Orchestrator (copilot namespace)
```bash
kubectl apply -k k8s/orchestrator/
```

### 3. Verify
```bash
# Check MCP servers
kubectl get pods -n observability -l component=mcp-server

# Check orchestrator
kubectl get pods -n copilot -l app=orchestrator

# Test orchestrator API
kubectl port-forward -n copilot svc/orchestrator-api 8080:8080
curl http://localhost:8080/health
```

## Monitoring

### Prometheus ServiceMonitors
```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: orchestrator
  namespace: copilot
spec:
  selector:
    matchLabels:
      app: orchestrator
  endpoints:
  - port: metrics
    interval: 30s
```

### Key Metrics
- `copilot_investigations_total` - Total investigations
- `copilot_investigation_duration_seconds` - Investigation latency
- `copilot_mcp_requests_total` - MCP server requests
- `copilot_mcp_errors_total` - MCP server errors
- `copilot_hypotheses_generated_total` - Hypotheses generated
- `copilot_correlation_gaps_total` - Correlation gaps detected

## Resumo

**MCP Servers**:
- Stateless, simples
- Apenas código de integração
- Sem prompts, steering ou specs
- Namespace: `observability`

**Orchestrator**:
- Stateful, inteligente
- Contém prompts, steering, specs, guardrails
- Usa LLM para raciocínio
- Namespace: `copilot`

**Steering/Prompts/Specs**:
- **PoC**: Bake into Docker image
- **Produção**: ConfigMaps ou Git Sync
- Sempre versionados e rastreáveis
